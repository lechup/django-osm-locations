"""
Caches used while parsing an OSM extract.

Importing a country means holding its whole node/way/relation graph somewhere
before it can be turned into geometries, and that graph is large: a country
extract easily has tens of millions of nodes. The structures here exist to keep
that affordable.

Three things dominate the footprint, and each has its own answer:

* the set of node/way ids an entry still needs -- ``IdSet``, a roaring bitmap
* the coordinates of every referenced node -- ``CoordsStore``, parallel arrays
* everything else (relations, ways, tagged nodes) -- the ``*Cache`` backends
"""
import logging
import os
from array import array
from itertools import count

from django.conf import settings

from ....conf import get_cache_dir

logger = logging.getLogger(__name__)


class IdSet:
    """
    A set of OSM ids.

    Only ``add`` and membership are ever needed, which makes this a good fit for
    a roaring bitmap: a CPython ``set`` of ints costs ~34 bytes per id, so the
    node precache alone runs to gigabytes on a country extract, while a bitmap
    stores the same ids in roughly 2 bytes each (far less when they are dense,
    which is what an extract looks like).

    Falls back to a plain ``set`` when pyroaring is not installed, so the
    importer still runs -- just hungrier.
    """

    __slots__ = ('_impl', 'backed_by_bitmap')

    def __init__(self):
        try:
            # BitMap64, not BitMap: OSM node ids passed 2**32 long ago and the
            # 32-bit variant raises OverflowError on any modern extract.
            from pyroaring import BitMap64
        except ImportError:
            self._impl = set()
            self.backed_by_bitmap = False
        else:
            self._impl = BitMap64()
            self.backed_by_bitmap = True

    def add(self, osm_id):
        self._impl.add(int(osm_id))

    def __contains__(self, osm_id):
        try:
            return int(osm_id) in self._impl
        except (ValueError, TypeError, OverflowError):
            return False

    def __len__(self):
        return len(self._impl)

    def __iter__(self):
        return iter(self._impl)


class CoordsStore:
    """
    Append-only ``osm_id -> (x, y)`` store for nodes that carry no tags.

    These are the bulk of an extract and they are perfectly homogeneous, so
    holding them as ``{id: ({}, (x, y))}`` is enormously wasteful -- about
    308 bytes per node once the dict entry, the tuple, the empty tags dict and
    two boxed floats are counted, or roughly 14 GB at 50M nodes.

    Three parallel numeric arrays cost about 24 bytes per node instead. Lookups
    binary-search a sorted copy built on first read; ids added after that point
    go to a small overflow dict, which stays tiny in practice because the coords
    pass is finished by then.
    """

    def __init__(self):
        self._ids = array('q')
        self._xs = array('d')
        self._ys = array('d')
        self._sorted_ids = None
        self._sorted_xs = None
        self._sorted_ys = None
        self._overflow = {}

    def add(self, osm_id, x, y):
        if self._sorted_ids is not None:
            self._overflow[int(osm_id)] = (x, y)
            return
        self._ids.append(int(osm_id))
        self._xs.append(float(x))
        self._ys.append(float(y))

    def _freeze(self):
        """
        Build the sorted view and release the builders.

        Peaks at roughly twice the final size while the permutation is applied;
        still far below what the dict representation costs at rest.
        """
        import numpy as np

        ids = np.frombuffer(memoryview(self._ids), dtype=np.int64)
        order = np.argsort(ids, kind='stable')
        sorted_ids = ids[order]
        del ids

        xs = np.frombuffer(memoryview(self._xs), dtype=np.float64)
        sorted_xs = xs[order]
        del xs

        ys = np.frombuffer(memoryview(self._ys), dtype=np.float64)
        sorted_ys = ys[order]
        del ys, order

        self._sorted_ids, self._sorted_xs, self._sorted_ys = sorted_ids, sorted_xs, sorted_ys
        # drop the builders; further adds go to the overflow dict
        self._ids = self._xs = self._ys = None

    def get(self, osm_id):
        osm_id = int(osm_id)
        if osm_id in self._overflow:
            return self._overflow[osm_id]

        if self._sorted_ids is None:
            if not len(self._ids):
                return None
            self._freeze()

        import numpy as np

        position = np.searchsorted(self._sorted_ids, osm_id)
        if position >= self._sorted_ids.size or self._sorted_ids[position] != osm_id:
            return None
        return float(self._sorted_xs[position]), float(self._sorted_ys[position])

    def __contains__(self, osm_id):
        return self.get(osm_id) is not None

    def __len__(self):
        stored = len(self._ids) if self._ids is not None else self._sorted_ids.size
        return int(stored) + len(self._overflow)

    def clear(self):
        self.__init__()


class CacheBase(object):
    def __init__(self, *args, **kwargs):
        # ids of nodes/ways that are part of relations and ways and therefore
        # still need to be cached when their pass comes around
        self.precache = IdSet()

    def set(self, key, value, *args, **kwargs):
        raise NotImplementedError

    def get(self, key, *args, **kwargs):
        raise NotImplementedError

    def commit(self):
        raise NotImplementedError

    def flush(self):
        raise NotImplementedError

    def exists(self, key):
        raise NotImplementedError

    def keys(self):
        raise NotImplementedError


class MemoryCache(CacheBase):
    def __init__(self, *args, **kwargs):
        super(MemoryCache, self).__init__(*args, **kwargs)
        self.cache = {}

    def set(self, key, value, *args, **kwargs):
        self.cache[key] = value

    def get(self, key, *args, **kwargs):
        return self.cache.get(key, *args, **kwargs)

    def commit(self):
        pass

    def flush(self):
        self.cache.clear()

    def exists(self, key):
        return key in self.cache

    def keys(self):
        return self.cache.keys()


class NodeCache(CacheBase):
    """
    Node storage split by whether the node carries tags.

    Only tagged nodes can ever become a Place, so they go to a normal cache
    backend; untagged nodes exist purely to supply coordinates to ways and
    relations, and go to a ``CoordsStore``.

    ``keys()`` deliberately yields only the tagged nodes. It is used solely to
    drive the import pass, and an untagged node can never satisfy
    ``_is_node_place`` -- walking tens of millions of them only to discard each
    one was pure overhead.
    """

    def __init__(self, backend_class=MemoryCache, *args, **kwargs):
        super(NodeCache, self).__init__(*args, **kwargs)
        self.tagged = backend_class()
        self.coords = CoordsStore()

    def set(self, key, value, *args, **kwargs):
        tags, coords = value
        if tags:
            self.tagged.set(key, value, *args, **kwargs)
        else:
            self.coords.add(key, coords[0], coords[1])

    def set_coords(self, key, x, y):
        """Store a coordinate-only node without building a tuple or tags dict."""
        self.coords.add(key, x, y)

    def get(self, key, *args, **kwargs):
        value = self.tagged.get(key)
        if value is not None:
            return value
        coords = self.coords.get(key)
        if coords is None:
            return None
        return {}, coords

    def exists(self, key):
        return self.tagged.exists(key) or key in self.coords

    def commit(self):
        self.tagged.commit()

    def flush(self):
        self.tagged.flush()
        self.coords.clear()

    def keys(self):
        return self.tagged.keys()


class MemcachedCache(CacheBase):
    """
    Reasonable time, takes less memory than in Python, result in some
    unhandled state (to much usage of swap + zombie process)
    """
    _cache_counter = count(0)

    def __init__(self, *args, **kwargs):
        from pylibmc import Client
        super(MemcachedCache, self).__init__(*args, **kwargs)
        self.cache_id = next(self._cache_counter)
        self.memcached_kwargs = getattr(settings, 'LOCATIONS_MEMCACHED_CACHE')[self.cache_id]
        self.memcached = Client(**self.memcached_kwargs)
        self.cache = {}
        self._set_count = 0
        self._keys = set()

    @classmethod
    def key_to_bytes(cls, key):
        return str(key)

    def set(self, key, value, *args, **kwargs):
        commit = kwargs.pop('commit', False)
        key = self.__class__.key_to_bytes(key)
        self.cache[key] = value
        self._keys.add(key)
        self._set_count += 1
        if self._set_count % 1000 == 0 or commit:
            self.commit()

    def get(self, key, *args, **kwargs):
        key = self.__class__.key_to_bytes(key)
        return self.memcached.get(key)

    def commit(self):
        if self.cache:
            self.memcached.set_multi(self.cache)
            self.cache.clear()

    def flush(self):
        self.memcached.flush_all()
        self.cache.clear()

    def exists(self, key):
        return self.get(key) is not None

    def keys(self):
        return list(self._keys)


class DiskCache(CacheBase):
    """
    Use reasonable amount of HDD 1GB osm.pbf -> 1,1GB on HDD, uses SQLite under the hood, takes too much time
    1GB osm.pbf -> 2 minutes for Relations, 40 minutes for Ways, 14,5 hours for Nodes and Coords
    """
    _cache_counter = count(0)

    def __init__(self, *args, **kwargs):
        from diskcache import Cache
        super(DiskCache, self).__init__(*args, **kwargs)
        # ids only -- the previous implementation kept str(key) for every entry,
        # which cost more memory than the values it was spilling to disk
        self._keys = IdSet()
        self.cache_id = next(self._cache_counter)
        self.diskcache = Cache(
            os.path.join(
                get_cache_dir(),
                '.disk_cache',
                str(self.cache_id)
            ),
            size_limit=50 * (2 ** 30)  # 50GB
        )
        self.diskcache.clear()

    def __del__(self):
        if getattr(self, 'diskcache', False):
            self.diskcache.clear()

    @classmethod
    def key_to_bytes(cls, key):
        return str(key)

    def keys(self):
        return list(self._keys)

    def set(self, key, value, *args, **kwargs):
        self._keys.add(key)
        self.diskcache.set(self.__class__.key_to_bytes(key), value, *args, **kwargs)

    def flush(self):
        self.diskcache.clear()

    def commit(self):
        pass

    def get(self, key, *args, **kwargs):
        key = self.__class__.key_to_bytes(key)
        return self.diskcache.get(key, *args, **kwargs)

    def exists(self, key):
        return key in self._keys


class FileCache(CacheBase):
    """
    Cosumes to much space, 1GB osm.pbf -> over 50GB on HDD
    """
    _cache_counter = count(0)

    def __init__(self, *args, **kwargs):
        from fcache.cache import FileCache as FileCacheClient
        super(FileCache, self).__init__(*args, **kwargs)
        self.cache_id = next(self._cache_counter)
        self.name = '{}.{}'.format(
            __name__,
            str(self.cache_id)
        )
        self.filecache = FileCacheClient(
            self.name,
            flag='n',
            app_cache_dir=os.path.join(
                get_cache_dir(),
                '.file_cache',
                self.name
            )
        )
        self.set_count = 0

    def __del__(self):
        if getattr(self, 'filecache', None):
            self.filecache.delete()

    @classmethod
    def key_to_bytes(cls, key):
        return str(key)

    def keys(self):
        return self.filecache.keys()

    def set(self, key, value, *args, **kwargs):
        key = self.__class__.key_to_bytes(key)
        self.filecache[key] = value
        self.set_count += 1
        if self.set_count % 1000 == 0:
            self.commit()

    def flush(self):
        self.filecache.delete()

    def commit(self):
        self.filecache.sync()

    def get(self, key, *args, **kwargs):
        key = self.__class__.key_to_bytes(key)
        return self.filecache.get(key, *args, **kwargs)

    def exists(self, key):
        key = self.__class__.key_to_bytes(key)
        return key in self.filecache

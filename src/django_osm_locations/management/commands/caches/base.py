import os
import logging

from ....conf import get_cache_dir
from itertools import count


logger = logging.getLogger(__name__)


class CacheBase(object):
    def __init__(self, *args, **kwargs):
        # used as a cache for ids of nodes we need to cache that
        # are part of relations and ways
        self.precache = set()

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
        pass

    def exists(self, key):
        return key in self.cache

    def keys(self):
        return self.cache.keys()


class MemcachedCache(CacheBase):
    """
    Reasonable time, takes less memory than in Python, result in some
    unhandled state (to much usage of swap + zombie process)
    """
    _cache_counter = count(0)

    def __init__(self, *args, **kwargs):
        from pylibmc import Client
        super(MemcachedCache, self).__init__(*args, **kwargs)
        self.cache_id = self._cache_counter.next()
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
        key = self.__class__.key_to_bytes(key)
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
        self.keys = set()
        self.cache_id = self._cache_counter.next()
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
        return self.keys

    def set(self, key, value, *args, **kwargs):
        key = self.__class__.key_to_bytes(key)
        self.keys.add(key)
        self.diskcache.set(key, value, *args, **kwargs)

    def flush(self):
        self.diskcache.clear()

    def commit(self):
        pass

    def get(self, key, *args, **kwargs):
        key = self.__class__.key_to_bytes(key)
        return self.diskcache.get(key, *args, **kwargs)

    def exists(self, key):
        key = self.__class__.key_to_bytes(key)
        return key in self.keys


class FileCache(CacheBase):
    """
    Cosumes to much space, 1GB osm.pbf -> over 50GB on HDD
    """
    _cache_counter = count(0)

    def __init__(self, *args, **kwargs):
        from fcache.cache import FileCache as FileCacheClient
        super(FileCache, self).__init__(*args, **kwargs)
        self.cache_id = self._cache_counter.next()
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
        if getattr(self, 'filecache'):
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


# as test showed only MemoryCache is usable in sane amount of time, we can still try to giva a shot:
# https://github.com/yinqiwen/ardb + leveldb

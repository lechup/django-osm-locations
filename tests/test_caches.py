"""
Cache backends used during import.

Worth testing directly: every non-memory backend was broken on Python 3 for
years (``itertools.count.next()``), which went unnoticed because nothing
constructed them, and they are exactly the backends reached for when an import
will not fit in RAM.
"""
import pytest

from django_osm_locations.management.commands.caches.base import (
    CoordsStore,
    IdSet,
    MemoryCache,
    NodeCache,
)


class TestIdSet:
    def test_add_and_membership(self):
        ids = IdSet()
        ids.add(1)
        ids.add(12_000_000_000)   # beyond 2**32
        assert 1 in ids
        assert 12_000_000_000 in ids
        assert 2 not in ids
        assert len(ids) == 2

    def test_handles_ids_beyond_32_bits(self):
        """BitMap (32-bit) raises OverflowError; BitMap64 is required."""
        ids = IdSet()
        for osm_id in [2**32, 2**33, 12_345_678_901]:
            ids.add(osm_id)
            assert osm_id in ids

    def test_negative_and_junk_are_not_members(self):
        ids = IdSet()
        ids.add(5)
        assert -1 not in ids
        assert None not in ids

    def test_duplicate_adds_do_not_grow_it(self):
        ids = IdSet()
        for _ in range(10):
            ids.add(7)
        assert len(ids) == 1


class TestCoordsStore:
    def test_round_trips_coordinates(self):
        store = CoordsStore()
        store.add(10, 1.5, 2.5)
        store.add(20, 3.5, 4.5)
        assert store.get(10) == (1.5, 2.5)
        assert store.get(20) == (3.5, 4.5)

    def test_missing_id_returns_none(self):
        store = CoordsStore()
        store.add(1, 0.0, 0.0)
        assert store.get(999) is None
        assert 999 not in store

    def test_unsorted_input_is_searchable(self):
        """Ids arrive in file order; the store sorts them before binary search."""
        store = CoordsStore()
        pairs = [(50, 5.0), (10, 1.0), (30, 3.0), (20, 2.0), (40, 4.0)]
        for osm_id, v in pairs:
            store.add(osm_id, v, -v)
        for osm_id, v in pairs:
            assert store.get(osm_id) == (v, -v)

    def test_add_after_first_read_still_visible(self):
        """The sorted view is built on first read; later adds must not be lost."""
        store = CoordsStore()
        store.add(1, 1.0, 1.0)
        assert store.get(1) == (1.0, 1.0)     # freezes
        store.add(2, 2.0, 2.0)                # overflow path
        assert store.get(2) == (2.0, 2.0)
        assert store.get(1) == (1.0, 1.0)
        assert len(store) == 2

    def test_large_ids(self):
        store = CoordsStore()
        store.add(12_000_000_000, 19.9, 50.0)
        assert store.get(12_000_000_000) == (19.9, 50.0)

    def test_empty_store(self):
        assert CoordsStore().get(1) is None

    def test_clear(self):
        store = CoordsStore()
        store.add(1, 1.0, 1.0)
        store.clear()
        assert store.get(1) is None
        assert len(store) == 0


class TestNodeCache:
    def test_tagged_and_untagged_are_split(self):
        cache = NodeCache(MemoryCache)
        cache.set(1, ({'place': 'city', 'name': 'Kraków'}, (19.9, 50.0)))
        cache.set_coords(2, 1.0, 2.0)

        assert len(cache.tagged.cache) == 1
        assert len(cache.coords) == 1

    def test_get_returns_the_same_shape_for_both_tiers(self):
        cache = NodeCache(MemoryCache)
        tags = {'place': 'city'}
        cache.set(1, (tags, (19.9, 50.0)))
        cache.set_coords(2, 1.0, 2.0)

        assert cache.get(1) == (tags, (19.9, 50.0))
        assert cache.get(2) == ({}, (1.0, 2.0))

    def test_set_with_empty_tags_goes_to_coords(self):
        cache = NodeCache(MemoryCache)
        cache.set(1, ({}, (1.0, 2.0)))
        assert len(cache.tagged.cache) == 0
        assert cache.get(1) == ({}, (1.0, 2.0))

    def test_exists_covers_both_tiers(self):
        cache = NodeCache(MemoryCache)
        cache.set(1, ({'place': 'city'}, (0.0, 0.0)))
        cache.set_coords(2, 1.0, 2.0)
        assert cache.exists(1)
        assert cache.exists(2)
        assert not cache.exists(3)

    def test_missing_returns_none(self):
        assert NodeCache(MemoryCache).get(42) is None

    def test_keys_yields_only_tagged_nodes(self):
        """
        keys() drives the import pass, and an untagged node can never be a
        Place -- iterating millions of them only to discard each was overhead.
        """
        cache = NodeCache(MemoryCache)
        cache.set(1, ({'place': 'city'}, (0.0, 0.0)))
        for osm_id in range(100, 200):
            cache.set_coords(osm_id, 1.0, 2.0)
        assert list(cache.keys()) == [1]

    def test_has_a_precache(self):
        cache = NodeCache(MemoryCache)
        cache.precache.add(5)
        assert 5 in cache.precache


@pytest.mark.parametrize('backend', [MemoryCache, NodeCache])
def test_backends_construct_on_python_3(backend):
    """
    Regression: MemcachedCache, DiskCache and FileCache all called
    itertools.count().next(), so constructing them raised AttributeError.
    """
    assert backend() is not None


def test_disk_cache_constructs(tmp_path, settings):
    """DiskCache additionally had self.keys shadowing its own keys() method."""
    diskcache = pytest.importorskip('diskcache')  # noqa: F841
    settings.LOCATIONS_CACHE_DIR = str(tmp_path)

    from django_osm_locations.management.commands.caches.base import DiskCache

    cache = DiskCache()
    cache.set(1, ('tags', 'value'))
    assert cache.exists(1)
    assert cache.get(1) == ('tags', 'value')
    assert list(cache.keys()) == [1]
    assert not cache.exists(2)


class TestBatchedLookup:
    def test_get_many_matches_get(self):
        store = CoordsStore()
        for osm_id in [50, 10, 30, 20, 40]:
            store.add(osm_id, osm_id / 10, -osm_id / 10)
        ids = [10, 20, 30, 40, 50]
        assert store.get_many(ids) == [store.get(i) for i in ids]

    def test_get_many_reports_missing_as_none(self):
        store = CoordsStore()
        store.add(1, 1.0, 2.0)
        assert store.get_many([1, 999, 1]) == [(1.0, 2.0), None, (1.0, 2.0)]

    def test_get_many_on_empty_store(self):
        assert CoordsStore().get_many([1, 2]) == [None, None]
        assert CoordsStore().get_many([]) == []

    def test_get_many_includes_overflow_entries(self):
        store = CoordsStore()
        store.add(1, 1.0, 1.0)
        store.get(1)                 # freeze
        store.add(2, 2.0, 2.0)       # overflow
        assert store.get_many([1, 2, 3]) == [(1.0, 1.0), (2.0, 2.0), None]

    def test_get_many_handles_ids_below_and_above_the_range(self):
        store = CoordsStore()
        for osm_id in [100, 200, 300]:
            store.add(osm_id, 1.0, 1.0)
        assert store.get_many([1, 100, 999]) == [None, (1.0, 1.0), None]

    def test_get_many_with_large_ids(self):
        store = CoordsStore()
        base = 12_000_000_000
        for offset in range(5):
            store.add(base + offset, offset, -offset)
        got = store.get_many([base + 3, base + 99])
        assert got == [(3.0, -3.0), None]


class TestNodeCacheBatch:
    def test_coords_for_spans_both_tiers(self):
        cache = NodeCache(MemoryCache)
        cache.set(1, ({'place': 'city'}, (10.0, 20.0)))   # tagged tier
        cache.set_coords(2, 1.0, 2.0)                     # coords tier
        assert cache.coords_for([1, 2, 3]) == [(10.0, 20.0), (1.0, 2.0), None]

    def test_coords_for_preserves_order_and_duplicates(self):
        cache = NodeCache(MemoryCache)
        cache.set_coords(7, 7.0, 7.0)
        cache.set_coords(8, 8.0, 8.0)
        assert cache.coords_for([8, 7, 8]) == [(8.0, 8.0), (7.0, 7.0), (8.0, 8.0)]

    def test_coords_for_empty(self):
        assert NodeCache(MemoryCache).coords_for([]) == []


class TestMembershipFastPath:
    def test_contains_all_true_when_every_id_present(self):
        store = CoordsStore()
        for osm_id in [3, 1, 2]:
            store.add(osm_id, 1.0, 1.0)
        assert store.contains_all([1, 2, 3]) is True

    def test_contains_all_false_when_any_missing(self):
        store = CoordsStore()
        store.add(1, 1.0, 1.0)
        assert store.contains_all([1, 2]) is False

    def test_contains_all_empty_is_true(self):
        assert CoordsStore().contains_all([]) is True

    def test_contains_all_on_empty_store(self):
        assert CoordsStore().contains_all([1]) is False

    def test_contains_all_considers_overflow(self):
        store = CoordsStore()
        store.add(1, 1.0, 1.0)
        store.get(1)                # freeze
        store.add(2, 2.0, 2.0)      # overflow
        assert store.contains_all([1, 2]) is True
        assert store.contains_all([1, 2, 3]) is False

    def test_contains_all_agrees_with_get_many(self):
        store = CoordsStore()
        for osm_id in [10, 20, 30]:
            store.add(osm_id, 1.0, 1.0)
        for probe in ([10], [10, 20], [10, 99], [99], [10, 20, 30]):
            expected = all(c is not None for c in store.get_many(probe))
            assert store.contains_all(probe) is expected, probe

    def test_has_all_coords_spans_both_tiers(self):
        cache = NodeCache(MemoryCache)
        cache.set(1, ({'place': 'city'}, (1.0, 2.0)))   # tagged only
        cache.set_coords(2, 3.0, 4.0)
        assert cache.has_all_coords([1, 2]) is True
        assert cache.has_all_coords([1, 2, 3]) is False

    def test_has_all_coords_empty(self):
        assert NodeCache(MemoryCache).has_all_coords([]) is True

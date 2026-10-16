"""
Two extracts read as one: Spain's Canary Islands come from Africa's.

The merge has to keep each file's order, write a shared object once, and do it
without holding either file in memory.
"""
import pytest

osmium = pytest.importorskip('osmium')

from django_osm_locations.utils.extracts import cut_extract, is_stale, merge_extracts  # noqa: E402

Node = osmium.osm.mutable.Node
Way = osmium.osm.mutable.Way
Relation = osmium.osm.mutable.Relation


def extract(path, objects):
    writer = osmium.SimpleWriter(str(path))
    for obj in objects:
        writer.add(obj)
    writer.close()
    return str(path)


def node(id, version=1, x=0.0):
    return Node(id=id, version=version, location=(x, 0.0), tags={'v': str(version)})


def contents(path):
    return [(obj.type_str(), obj.id, obj.version) for obj in osmium.FileProcessor(path)]


@pytest.fixture
def mainland(tmp_path):
    return extract(tmp_path / 'spain.osm.pbf', [
        node(1), node(2), node(5),
        Way(id=10, version=1, nodes=[1, 2]),
        Relation(id=100, version=1, members=[('w', 10, 'outer')]),
    ])


@pytest.fixture
def islands(tmp_path):
    return extract(tmp_path / 'canary-islands.osm.pbf', [
        node(3), node(5), node(7),
        Way(id=11, version=1, nodes=[3, 7]),
        # the country's relation, newer in this extract
        Relation(id=100, version=2, members=[('w', 10, 'outer'), ('w', 11, 'outer')]),
    ])


class TestMerge:
    def test_everything_from_both_in_one_sorted_file(self, tmp_path, mainland, islands):
        out = str(tmp_path / 'spain-merged.osm.pbf')
        merge_extracts([mainland, islands], out)
        assert contents(out) == [
            ('n', 1, 1), ('n', 2, 1), ('n', 3, 1), ('n', 5, 1), ('n', 7, 1),
            ('w', 10, 1), ('w', 11, 1),
            ('r', 100, 2),
        ]

    def test_an_object_in_both_is_written_once_in_its_newest_version(
            self, tmp_path, mainland, islands):
        out = str(tmp_path / 'spain-merged.osm.pbf')
        written = merge_extracts([mainland, islands], out)
        assert written == 8
        members = [len(obj.members) for obj in osmium.FileProcessor(out, osmium.osm.RELATION)]
        assert members == [2]

    def test_the_tags_come_through(self, tmp_path, mainland, islands):
        out = str(tmp_path / 'spain-merged.osm.pbf')
        merge_extracts([mainland, islands], out)
        tags = {obj.id: obj.tags.get('v') for obj in osmium.FileProcessor(out, osmium.osm.NODE)}
        assert tags[7] == '1'

    def test_no_partial_file_is_left_behind(self, tmp_path, mainland, islands):
        out = tmp_path / 'spain-merged.osm.pbf'
        merge_extracts([mainland, islands], str(out))
        assert [p.name for p in tmp_path.iterdir() if p.name.endswith('.part')] == []


class TestStale:
    def test_missing_is_stale(self, tmp_path, mainland):
        assert is_stale(str(tmp_path / 'nothing.osm.pbf'), [mainland])

    def test_older_than_a_source_is_stale(self, tmp_path, mainland, islands):
        import os
        out = str(tmp_path / 'spain-merged.osm.pbf')
        merge_extracts([mainland, islands], out)
        assert not is_stale(out, [mainland, islands])
        os.utime(islands, (os.path.getmtime(out) + 10,) * 2)
        assert is_stale(out, [mainland, islands])


class TestCut:
    """San Marino's corner of Italy, without indexing the rest of it."""

    @pytest.fixture
    def italy(self, tmp_path):
        return extract(tmp_path / 'italy.osm.pbf', [
            node(1, x=0.5), node(2, x=0.6),      # inside the corner
            node(3, x=5.0),                      # outside, on a way that enters it
            node(4, x=9.0), node(5, x=9.5),      # far away
            Way(id=10, version=1, nodes=[1, 2]),
            Way(id=11, version=1, nodes=[2, 3]),
            Way(id=12, version=1, nodes=[4, 5]),
            Relation(id=100, version=1, members=[('w', 10, 'outer'), ('w', 11, 'outer')]),
            Relation(id=101, version=1, members=[('w', 12, 'outer')]),
            # reaching past the corner: comes through with members missing
            Relation(id=102, version=1, members=[('w', 11, 'outer'), ('w', 12, 'outer')]),
        ])

    def test_the_corner_and_what_it_touches(self, tmp_path, italy):
        out = str(tmp_path / 'san-marino-cut.osm.pbf')
        cut_extract(italy, (0.0, -1.0, 1.0, 1.0), out)
        assert [(t, i) for t, i, _ in contents(out)] == [
            ('n', 1), ('n', 2), ('n', 3), ('w', 10), ('w', 11), ('r', 100), ('r', 102),
        ]

    def test_a_way_leaving_the_corner_keeps_its_far_node(self, tmp_path, italy):
        out = str(tmp_path / 'cut.osm.pbf')
        cut_extract(italy, (0.0, -1.0, 1.0, 1.0), out)
        far = [obj.location.lon for obj in osmium.FileProcessor(out, osmium.osm.NODE) if obj.id == 3]
        assert far == [5.0]

    def test_an_empty_corner_writes_an_empty_file(self, tmp_path, italy):
        out = str(tmp_path / 'cut.osm.pbf')
        assert cut_extract(italy, (50.0, 50.0, 51.0, 51.0), out) == 0
        assert contents(out) == []

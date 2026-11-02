"""
A pack's merged rows' OSM ids, read back from the extract it was built from.

A pack built before ``osm.merged_*_ids`` has none, and an old slug ending in
a merged row's id needs them. The extract here holds what the Poland pack
did: Polna in two ways, the second joined into the first; a village drawn as
a node inside the place Wola, and the county of the same name; an address
point beside the building that stayed.

Needs the OpenSearch container from docker-compose.test.yml:

    docker compose -f docker-compose.test.yml up -d
    pytest -m search
"""
import uuid

import pytest
from django.contrib.gis.geos import LineString, MultiLineString, MultiPolygon, Point
from django.core.management import call_command

from django_osm_locations.models import Address, Place, PlaceSubarea, Street
from django_osm_locations.search.indexer import Reindexer
from django_osm_locations.search.indexes import INDEXES

from .conftest import square

osmium = pytest.importorskip('osmium')

pytestmark = [pytest.mark.search, pytest.mark.django_db]

Node = osmium.osm.mutable.Node
Way = osmium.osm.mutable.Way
Relation = osmium.osm.mutable.Relation

POLSKA = ['Polska', 'polska']
WOLA = ['Wola', 'wola']
POLNA = [(20.0, 50.0), (20.0005, 50.0), (20.001, 50.0), (20.0015, 50.0), (20.002, 50.0)]


def extract(path):
    """The objects in the order an extract has them: nodes, ways, relations."""
    writer = osmium.SimpleWriter(str(path))
    for i, at in enumerate(POLNA, start=1):
        writer.add(Node(id=i, location=at))
    writer.add(Node(id=600, location=(20.0, 50.0), tags={'place': 'village', 'name': 'Wola'}))
    writer.add(Node(id=601, location=(25.0, 50.0), tags={'place': 'village', 'name': 'Wola'}))
    writer.add(Node(id=701, location=(20.0006, 50.0001),
                    tags={'addr:street': 'Polna', 'addr:housenumber': '5'}))
    for i, at in enumerate([(20.0004, 50.0001), (20.0005, 50.0001), (20.0005, 50.0002)], start=10):
        writer.add(Node(id=i, location=at))
    writer.add(Node(id=20, location=(21.0, 50.0)))
    writer.add(Node(id=21, location=(21.001, 50.0)))
    writer.add(Node(id=22, location=(21.002, 50.0)))
    street = {'highway': 'residential', 'name': 'Polna'}
    writer.add(Way(id=100, nodes=[1, 2, 3], tags=street))
    writer.add(Way(id=101, nodes=[3, 4, 5], tags=street))       # joined into 100
    writer.add(Way(id=102, nodes=[20, 21, 22], tags=street))    # a Polna in no pack
    writer.add(Way(id=700, nodes=[10, 11, 12, 10],
                   tags={'building': 'house', 'addr:street': 'Polna', 'addr:housenumber': '5'}))
    writer.add(Relation(id=800, members=[('n', 600, 'label'), ('w', 100, 'outer')],
                        tags={'boundary': 'administrative', 'admin_level': '6', 'name': 'Wola'}))
    writer.add(Relation(id=900, members=[('w', 102, 'outer')],
                        tags={'boundary': 'administrative', 'admin_level': '9', 'name': 'Zakole'}))
    writer.close()
    return str(path)


@pytest.fixture
def pack(settings, tmp_path):
    from django_osm_locations.search.client import get_client

    client = get_client()
    try:
        client.info()
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip('OpenSearch is not reachable: {}'.format(exc))
    prefix = 'test-merged-ids-{}'.format(uuid.uuid4().hex[:8])
    Place.objects.create(name='Wola', slug='wola', relation_osm_id=500,
                         border=MultiPolygon(square(19.95, 49.95, 0.1)), country=[POLSKA])
    Street.objects.create(name='Polna', slug='polna-wola', way_osm_id=100, country=[POLSKA],
                          place=[WOLA], lines=MultiLineString(LineString(*POLNA)))
    Address.objects.create(name='Polna 5', slug='polna-wola-5', house_number='5', street_name='Polna',
                           way_osm_id=700, center=Point(20.00047, 50.00013), country=[POLSKA],
                           place=[WOLA], street=[['Polna', 'polna-wola']])
    PlaceSubarea.objects.create(name='Zakole', slug='zakole', relation_osm_id=901,
                                border=MultiPolygon(square(20.0, 50.0, 0.01)),
                                country=[POLSKA], place=[WOLA])
    for name in ('places', 'place-subareas', 'country-subareas', 'streets', 'addresses'):
        reindexer = Reindexer(INDEXES[name], client=client, prefix=prefix)
        reindexer.create()
        reindexer.populate()
        reindexer.promote()
    settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = prefix
    settings.LOCATIONS_CACHE_DIR = str(tmp_path)
    yield client, prefix, extract(tmp_path / 'poland.osm.pbf')
    client.indices.delete(index='{}-*'.format(prefix), ignore_unavailable=True)


def osm(client, prefix, name, slug):
    hits = client.search(index='{}-{}'.format(prefix, name), body={
        'query': {'term': {'slug': slug}}})['hits']['hits']
    return hits[0]['_source']['osm']


@pytest.fixture
def repaired(pack):
    client, prefix, path = pack
    call_command('locations_repair_merged_ids', 'poland', path)
    return lambda name, slug: osm(client, prefix, name, slug)


class TestTheRepair:
    def test_a_piece_of_the_street_on_its_line(self, repaired):
        street = repaired('streets', 'polna-wola')
        assert (street['way_id'], street['merged_way_ids']) == (100, [101])

    def test_a_village_inside_the_place(self, repaired):
        assert repaired('places', 'wola')['merged_node_ids'] == [600]

    def test_the_county_that_is_the_place(self, repaired):
        """Placed by its label node, and merged into the place, not into a county."""
        assert repaired('places', 'wola')['merged_relation_ids'] == [800]

    def test_a_village_far_away_is_not_this_one(self, repaired):
        assert 601 not in repaired('places', 'wola')['merged_node_ids']

    def test_the_address_point_beside_the_building(self, repaired):
        house = repaired('addresses', 'polna-wola-5')
        assert (house['way_id'], house['merged_node_ids']) == (700, [701])

    def test_a_district_with_nothing_to_place_it_by_its_name_alone(self, repaired):
        """Zakole is the only one of its name, so the name is enough."""
        assert repaired('place-subareas', 'zakole')['merged_relation_ids'] == [900]

    def test_a_street_elsewhere_is_nobodys_piece(self, repaired):
        assert 102 not in repaired('streets', 'polna-wola')['merged_way_ids']

    def test_twice_changes_nothing(self, pack, repaired, capsys):
        client, prefix, path = pack
        capsys.readouterr()
        call_command('locations_repair_merged_ids', 'poland', path)
        assert '0 locations given' in capsys.readouterr().out

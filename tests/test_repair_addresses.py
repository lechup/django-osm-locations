"""
Repairing a pack's streets and addresses, and coming to what a new
recalculation comes to.

The same rows go two ways: through the recalculation as it was, then into
an index and through locations_repair_addresses; and through the
recalculation as it is now, then into an index. The two indexes have to hold
the same streets and the same houses.

Needs the OpenSearch container from docker-compose.test.yml:

    docker compose -f docker-compose.test.yml up -d
    pytest -m search
"""
import itertools
import uuid

import pytest
from django.contrib.gis.geos import LineString, MultiLineString, Point
from django.core.management import call_command

from django_osm_locations.management.commands.locations_recalculate_denormalized_fields import (
    Command as Denorm,
)
from django_osm_locations.models import Address, Street
from django_osm_locations.search.indexer import Reindexer
from django_osm_locations.search.indexes import INDEXES

pytestmark = [pytest.mark.search, pytest.mark.django_db]

pytest.importorskip('opensearchpy', reason='needs opensearch-py')

POLSKA = ['Polska', 'polska']
WOLA = ['Wola', 'wola']
KOWALE = ['Kowale', 'kowale']
EAST = 1 / (111320 * 0.6428)
NORTH = 1 / 110540
OSM_IDS = itertools.count(1)


@pytest.fixture(scope='module')
def opensearch():
    from django_osm_locations.search.client import get_client

    client = get_client()
    try:
        client.info()
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip('OpenSearch is not reachable: {}'.format(exc))
    return client


def street(name, slug, x, y, metres=100):
    return Street.objects.create(
        name=name, slug=slug, lines=MultiLineString(LineString((x, y), (x + metres * EAST, y))),
        length=metres, country=[POLSKA], place=[WOLA])


def address(number, x, y, street_name='Polna', place=WOLA, way=False, postcode=''):
    return Address.objects.create(
        name='{} {}'.format(street_name or place[0], number), house_number=number,
        street_name=street_name, center=Point(x, y), country=[POLSKA], place=[place],
        postcode=postcode, **{'way_osm_id' if way else 'node_osm_id': next(OSM_IDS)})


def build_wola():
    """A village drawn as a point, its Polna in two pieces 300 m apart."""
    street('Polna', 'polna-wola', 20.0, 50.0)
    street('Polna', 'polna-wola-123', 20.0 + 400 * EAST, 50.0)
    street('Leśna', 'lesna-wola', 20.0, 50.0 + 2000 * NORTH)
    address('1', 20.0 + 450 * EAST, 50.0 + 20 * NORTH)          # by the second piece
    address('5', 20.0 + 50 * EAST, 50.0 + 300 * NORTH)          # set back, 300 m off
    address('7', 20.0 + 30 * EAST, 50.0 + 10 * NORTH, way=True)  # a building
    address('7', 20.0 + 42 * EAST, 50.0 + 10 * NORTH, postcode='34-100')  # a shop in it
    address('9', 20.0 + 10 * EAST, 50.0 + 5 * NORTH)
    address('9', 20.0 + 6000 * EAST, 50.0 + 5 * NORTH)         # another house 6 km off
    address('3', 20.0 + 30 * EAST, 50.0 + 2250 * NORTH, 'Leśna')
    address('2', 21.0, 50.0, '', KOWALE)
    address('2', 21.0 + 10 * EAST, 50.0, '', KOWALE)


def index(opensearch, prefix):
    for name in ('streets', 'addresses'):
        reindexer = Reindexer(INDEXES[name], client=opensearch, prefix=prefix)
        reindexer.create()
        reindexer.populate()
        reindexer.promote()


def contents(opensearch, prefix):
    def hits(name):
        return opensearch.search(index='{}-{}'.format(prefix, name), body={
            'size': 100, 'query': {'match_all': {}}})['hits']['hits']

    streets = sorted((h['_source']['slug'], round(h['_source']['length']),
                      tuple(round(c, 6) for c in h['_source']['bbox']))
                     for h in hits('streets'))
    addresses = sorted((h['_source']['slug'], tuple(h['_source']['ancestry_slugs']),
                        round(h['_source']['center']['lon'], 7), round(h['_source']['center']['lat'], 7),
                        h['_source']['postcode'])
                       for h in hits('addresses'))
    return streets, addresses


@pytest.fixture
def both(opensearch, settings):
    """``(repaired, recalculated)``: the contents of each index."""
    before, after = ('test-repair-{}-{}'.format(kind, uuid.uuid4().hex[:8])
                     for kind in ('before', 'after'))
    build_wola()
    # as it was: linked within 100 m, slugged, indexed -- and then repaired
    Denorm.update_address_street_field()
    Denorm.update_address_slugs()
    index(opensearch, before)
    settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = before
    call_command('locations_repair_addresses')

    # as it is now, from the same rows
    Address.objects.update(street=[])
    Denorm.merge_namesake_streets()
    Denorm.update_address_street_field()
    Denorm.link_addresses_to_streets_in_their_place()
    Denorm.merge_repeated_houses()
    Denorm.update_address_slugs()
    index(opensearch, after)
    yield contents(opensearch, before), contents(opensearch, after)
    for prefix in (before, after):
        opensearch.indices.delete(index='{}-*'.format(prefix), ignore_unavailable=True)


class TestTheRepair:
    def test_comes_to_what_the_recalculation_does(self, both):
        repaired, recalculated = both
        assert repaired == recalculated

    def test_one_polna(self, both):
        (streets, _), _ = both
        assert [slug for slug, _, _ in streets] == ['lesna-wola', 'polna-wola']

    def test_the_houses_on_the_piece_that_went_are_on_the_street_that_stays(self, both):
        (_, addresses), _ = both
        assert 'polna-wola-1' in [a[0] for a in addresses]

    def test_the_house_set_back_has_its_street(self, both):
        (_, addresses), _ = both
        (five,) = [a for a in addresses if a[0] == 'polna-wola-5']
        assert 'polna-wola' in five[1]

    def test_the_building_and_the_shop_are_one_house_where_the_building_is(self, both):
        (_, addresses), _ = both
        (seven,) = [a for a in addresses if a[0] == 'polna-wola-7']
        assert seven[2] == round(20.0 + 30 * EAST, 7)
        assert seven[4] == '34-100'

    def test_the_house_6_km_off_is_another(self, both):
        (_, addresses), _ = both
        assert [a[0] for a in addresses].count('polna-wola-9') == 2

    def test_village_houses_with_no_street(self, both):
        (_, addresses), _ = both
        assert [a[0] for a in addresses].count('kowale-2') == 1

    def test_twice_changes_nothing_more(self, both, opensearch, settings):
        call_command('locations_repair_addresses')
        assert contents(opensearch, settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX) == both[0]

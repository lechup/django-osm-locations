"""
Telling apart a pack's houses that share a slug, and coming to what the
recalculation comes to.

Østergade 12 in Rudkøbing and in Bagenkop, on one street; kearns-5935, on
two streets neither is linked to. Indexed as the packs were, then repaired --
and slugged and indexed as the recalculation does it now.

Needs the OpenSearch container from docker-compose.test.yml:

    docker compose -f docker-compose.test.yml up -d
    pytest -m search
"""
import uuid

import pytest
from django.contrib.gis.geos import Point
from django.core.management import call_command
from rest_framework.test import APIRequestFactory

from django_osm_locations.api.views import DetailView
from django_osm_locations.management.commands.locations_recalculate_denormalized_fields import (
    Command as Denorm,
)
from django_osm_locations.models import Address
from django_osm_locations.search.indexer import Reindexer
from django_osm_locations.search.indexes import INDEXES

pytestmark = [pytest.mark.search, pytest.mark.django_db]

pytest.importorskip('opensearchpy', reason='needs opensearch-py')

DANMARK = ['Danmark', 'danmark']
LANGELAND = ['Langeland', 'langeland']
KEARNS = ['Kearns', 'kearns']


@pytest.fixture(scope='module')
def opensearch():
    from django_osm_locations.search.client import get_client

    client = get_client()
    try:
        client.info()
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip('OpenSearch is not reachable: {}'.format(exc))
    return client


def house(number, x, settlement, street_name, place, slug, street=None):
    linked = {'street': [street]} if street else {}
    return Address.objects.create(
        name='{} {}'.format(street_name, number), house_number=number, street_name=street_name,
        settlement_name=settlement, center=Point(x, 55.0), country=[DANMARK], place=[place],
        slug=slug, node_osm_id=int(x * 1000), **linked)


def index(opensearch, prefix):
    reindexer = Reindexer(INDEXES['addresses'], client=opensearch, prefix=prefix)
    reindexer.create()
    reindexer.populate()
    reindexer.promote()


def slugs(opensearch, prefix):
    return sorted((h['_source']['slug'], h['_source'].get('shared_slug'))
                  for h in opensearch.search(index='{}-addresses'.format(prefix),
                                             body={'size': 100})['hits']['hits'])


@pytest.fixture
def both(opensearch, settings):
    """``(repaired, recalculated)``: the prefixes of each index."""
    ostergade = ['Østergade', 'ostergade']
    house('12', 10.70, 'Rudkøbing', 'Østergade', LANGELAND, 'ostergade-12', ostergade)
    house('12', 10.71, 'Bagenkop', 'Østergade', LANGELAND, 'ostergade-12', ostergade)
    house('14', 10.70, 'Rudkøbing', 'Østergade', LANGELAND, 'ostergade-14', ostergade)
    house('5935', 10.80, 'Kearns', 'South Dewdrops Drive', KEARNS, 'kearns-5935')
    house('5935', 10.81, 'Kearns', 'South Salem Avenue', KEARNS, 'kearns-5935')
    before, after = ('test-slugs-{}-{}'.format(kind, uuid.uuid4().hex[:8])
                     for kind in ('before', 'after'))
    index(opensearch, before)
    settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = before
    call_command('locations_repair_slugs')
    Denorm.qualify_shared_address_slugs()
    index(opensearch, after)
    yield before, after
    for prefix in (before, after):
        opensearch.indices.delete(index='{}-*'.format(prefix), ignore_unavailable=True)


class TestTheRepair:
    def test_each_house_has_its_own(self, opensearch, both):
        assert slugs(opensearch, both[0]) == [
            ('kearns-5935-south-dewdrops-drive', 'kearns-5935'),
            ('kearns-5935-south-salem-avenue', 'kearns-5935'),
            ('ostergade-12-bagenkop', 'ostergade-12'),
            ('ostergade-12-rudkobing', 'ostergade-12'),
            ('ostergade-14', None),
        ]

    def test_comes_to_what_the_recalculation_does(self, opensearch, both):
        assert slugs(opensearch, both[0]) == slugs(opensearch, both[1])

    def test_the_slug_they_shared_names_them(self, opensearch, both):
        request = APIRequestFactory().get('/api/addresses/ostergade-12/')
        response = DetailView().get(DetailView().initialize_request(request), 'addresses',
                                    'ostergade-12')
        assert response.status_code == 409
        assert sorted(c['slug'] for c in response.data['candidates']) == [
            'ostergade-12-bagenkop', 'ostergade-12-rudkobing']

    def test_twice_changes_nothing_more(self, opensearch, both, capsys):
        call_command('locations_repair_slugs')
        assert '0 rewritten' in capsys.readouterr().out

    def test_a_house_alone_again_has_it_bare(self, opensearch, both):
        opensearch.delete_by_query(index='{}-addresses'.format(both[0]), refresh=True, body={
            'query': {'term': {'slug': 'ostergade-12-bagenkop'}}})
        call_command('locations_repair_slugs')
        assert ('ostergade-12', None) in slugs(opensearch, both[0])

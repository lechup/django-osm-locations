"""
Marking a pack's twins, and coming to what the indexer comes to.

Deurne, a point of 86,297 people inside Deurne, the district of Antwerpen:
indexed as the packs were, with neither marked, then repaired -- and indexed
as it is now. Search finds the district alone.

Needs the OpenSearch container from docker-compose.test.yml:

    docker compose -f docker-compose.test.yml up -d
    pytest -m search
"""
import uuid

import pytest
from django.contrib.gis.geos import MultiPolygon, Point
from django.core.management import call_command

from django_osm_locations.api.queries import build_body
from django_osm_locations.models import Place, PlaceSubarea
from django_osm_locations.search.indexer import Reindexer
from django_osm_locations.search.indexes import INDEXES, LocationIndex
from django_osm_locations.utils.twins import TWIN_OF

from .conftest import square

pytestmark = [pytest.mark.search, pytest.mark.django_db]

pytest.importorskip('opensearchpy', reason='needs opensearch-py')

ANTWERPEN = ['Antwerpen', 'antwerpen']


@pytest.fixture(scope='module')
def opensearch():
    from django_osm_locations.search.client import get_client

    client = get_client()
    try:
        client.info()
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip('OpenSearch is not reachable: {}'.format(exc))
    return client


def index(opensearch, prefix):
    for name in ('places', 'place-subareas'):
        reindexer = Reindexer(INDEXES[name], client=opensearch, prefix=prefix)
        reindexer.create()
        reindexer.populate()
        reindexer.promote()


def sources(opensearch, prefix, name):
    return {h['_source']['slug']: h['_source'] for h in opensearch.search(
        index='{}-{}'.format(prefix, name), body={'size': 100})['hits']['hits']}


@pytest.fixture
def both(opensearch, settings, monkeypatch):
    """``(repaired, indexed)``: the prefixes of each index."""
    PlaceSubarea.objects.create(name='Deurne', slug='deurne', place=[ANTWERPEN],
                                border=MultiPolygon(square(4.45, 51.2, 0.03)))
    PlaceSubarea.objects.create(name='Borgerhout', slug='borgerhout', place=[ANTWERPEN],
                                border=MultiPolygon(square(4.40, 51.2, 0.03)))
    Place.objects.create(name='Deurne', slug='deurne-node', place=[ANTWERPEN],
                         center=Point(4.46, 51.21), population=86297)
    Place.objects.create(name='Deurne', slug='deurne-nb', place=[['Deurne', 'deurne-nl']],
                         center=Point(5.79, 51.46), population=32000)
    before, after = ('test-twins-{}-{}'.format(kind, uuid.uuid4().hex[:8])
                     for kind in ('before', 'after'))
    with monkeypatch.context() as m:
        # as the packs were indexed: no twins
        m.setattr(LocationIndex, 'twin_fields', lambda self, ids: {})
        index(opensearch, before)
    settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = before
    call_command('locations_repair_twins')
    index(opensearch, after)
    yield before, after
    for prefix in (before, after):
        opensearch.indices.delete(index='{}-*'.format(prefix), ignore_unavailable=True)


class TestTheRepair:
    def test_the_point_is_the_districts_twin(self, opensearch, both):
        assert sources(opensearch, both[0], 'places')['deurne-node'][TWIN_OF] == 'deurne'

    def test_not_its_namesake_elsewhere(self, opensearch, both):
        assert not sources(opensearch, both[0], 'places')['deurne-nb'].get(TWIN_OF)

    def test_the_district_has_its_people(self, opensearch, both):
        assert sources(opensearch, both[0], 'place-subareas')['deurne']['population'] == 86297

    def test_comes_to_what_the_indexer_does(self, opensearch, both):
        def marks(prefix):
            places = sources(opensearch, prefix, 'places')
            districts = sources(opensearch, prefix, 'place-subareas')
            return ({slug: s.get(TWIN_OF) for slug, s in places.items()},
                    {slug: s.get('population') for slug, s in districts.items()})
        assert marks(both[0]) == marks(both[1])

    def test_search_finds_the_district_alone(self, opensearch, both):
        body, _, _ = build_body({'q': 'Antwerpen Deurne'})
        hits = opensearch.search(index='{0}-places,{0}-place-subareas'.format(both[0]),
                                 body=body)['hits']['hits']
        assert [(h['_source']['type'], h['_source']['slug']) for h in hits] == [
            ('place-subareas', 'deurne')]

    def test_twice_changes_nothing_more(self, opensearch, both, settings, capsys):
        call_command('locations_repair_twins')
        assert '0 marked or unmarked' in capsys.readouterr().out

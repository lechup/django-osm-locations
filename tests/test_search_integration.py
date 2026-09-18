"""
End-to-end: PostGIS -> reindex -> alias -> API.

Needs the OpenSearch container from docker-compose.test.yml, so it is opt-in:

    docker compose -f docker-compose.test.yml up -d
    pytest -m search
"""
import uuid

import pytest
from django.contrib.gis.geos import LineString, MultiLineString, MultiPolygon

from django_osm_locations.models import Country, Place, Street
from django_osm_locations.search.indexer import Reindexer
from django_osm_locations.search.indexes import INDEXES

from .conftest import square

pytestmark = [pytest.mark.search, pytest.mark.django_db]

pytest.importorskip('opensearchpy', reason='needs opensearch-py')


@pytest.fixture(scope='session')
def opensearch():
    from django_osm_locations.search.client import get_client

    client = get_client()
    try:
        client.info()
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip('OpenSearch is not reachable: {}'.format(exc))
    return client


@pytest.fixture
def prefix(opensearch):
    """A private index prefix per test, torn down afterwards."""
    value = 'test-{}'.format(uuid.uuid4().hex[:8])
    yield value
    opensearch.indices.delete(index='{}-*'.format(value), ignore_unavailable=True)


@pytest.fixture
def populated(db):
    poland = Country.objects.create(
        name='Polska', iso2='PL', iso3='POL', un=616,
        border=MultiPolygon(square(0, 0, 10)),
    )
    krakow = Place.objects.create(name='Kraków', border=MultiPolygon(square(1, 1)))
    munchen = Place.objects.create(name='München', border=MultiPolygon(square(3, 3)))
    merged = Place.objects.create(name='Duplicate', border=MultiPolygon(square(5, 5)))
    merged.mark_as_merged()
    street = Street.objects.create(
        name='Floriańska',
        lines=MultiLineString(LineString((1.1, 1.1), (1.2, 1.2))),
        place=[['Kraków', 'krakow']],
    )
    return {'country': poland, 'krakow': krakow, 'munchen': munchen,
            'merged': merged, 'street': street}


def reindex(name, prefix, client):
    reindexer = Reindexer(INDEXES[name], client=client, prefix=prefix)
    reindexer.create()
    reindexer.populate()
    reindexer.promote()
    return reindexer


class TestReindex:
    def test_indexes_every_row(self, populated, prefix, opensearch):
        reindexer = reindex('places', prefix, opensearch)
        # 3 places exist, 1 is merged and therefore not indexed
        assert reindexer.database_count() == 2
        assert reindexer.indexed_count() == 2

    def test_merged_rows_stay_in_the_database(self, populated, prefix, opensearch):
        reindex('places', prefix, opensearch)
        assert Place.objects.count() == 3, 'reindexing must not delete anything'

    def test_alias_points_at_the_new_index(self, populated, prefix, opensearch):
        reindexer = reindex('places', prefix, opensearch)
        assert reindexer.aliased_indexes() == [reindexer.index_name]
        assert reindexer.live_count() == 2

    def test_rebuild_swaps_the_alias_and_keeps_the_old_index(self, populated, prefix, opensearch):
        first = reindex('places', prefix, opensearch)
        Place.objects.create(name='Gdańsk', border=MultiPolygon(square(7, 7)))

        second = Reindexer(INDEXES['places'], client=opensearch, prefix=prefix,
                           suffix='rebuild')
        second.create()
        second.populate()
        replaced = second.promote()

        assert second.live_count() == 3
        assert first.index_name in replaced
        assert opensearch.indices.exists(index=first.index_name), \
            'the previous index is the rollback path and must survive'

    def test_geometry_round_trips(self, populated, prefix, opensearch):
        reindexer = reindex('places', prefix, opensearch)
        hit = opensearch.search(
            index=reindexer.alias,
            body={'query': {'term': {'slug': 'krakow'}}},
        )['hits']['hits'][0]['_source']
        assert hit['geometry']['type'] == 'MultiPolygon'
        assert hit['center']['lon']


class TestSearchAPI:
    @pytest.fixture(autouse=True)
    def indexed(self, populated, prefix, opensearch, settings):
        settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = prefix
        # the test client talks to "testserver"; without this the subdomain
        # middleware treats it as a foreign host and 301s every request away
        settings.ROOT_URL = 'testserver'
        for name in ('countries', 'places', 'streets'):
            reindex(name, prefix, opensearch)

    def test_search_across_types(self, client):
        body = client.get('/api/search/', {'q': 'krak'}).json()
        assert body['total'] >= 1
        assert any(hit['slug'] == 'krakow' for hit in body['results'])

    def test_prefix_matching(self, client):
        """The whole point of the edge-ngram analyser."""
        for query in ('k', 'kra', 'krakow'):
            body = client.get('/api/search/', {'q': query, 'type': 'places'}).json()
            assert any(h['slug'] == 'krakow' for h in body['results']), query

    def test_umlaut_search(self, client):
        """asciifolding means München is reachable both ways."""
        for query in ('münch', 'munch'):
            body = client.get('/api/search/', {'q': query, 'type': 'places'}).json()
            assert any(h['slug'] == 'munchen' for h in body['results']), query

    def test_type_filter(self, client):
        body = client.get('/api/search/', {'type': 'streets'}).json()
        assert all(hit['type'] == 'streets' for hit in body['results'])

    def test_unknown_type_is_a_400(self, client):
        response = client.get('/api/search/', {'type': 'villages'})
        assert response.status_code == 400
        assert 'villages' in response.json()['detail']

    def test_per_type_endpoint(self, client):
        body = client.get('/api/places/').json()
        assert body['total'] == 2
        assert all(hit['type'] == 'places' for hit in body['results'])

    def test_merged_rows_are_not_searchable(self, client):
        body = client.get('/api/search/', {'q': 'Duplicate'}).json()
        assert not any(hit['slug'] == 'duplicate' for hit in body['results'])

    def test_within_filters_by_ancestor(self, client):
        body = client.get('/api/streets/', {'within': 'krakow'}).json()
        assert body['total'] == 1
        assert body['results'][0]['slug'] == 'florianska'

    def test_bbox_filter(self, client):
        inside = client.get('/api/places/', {'bbox': '0.5,0.5,2.5,2.5'}).json()
        assert [h['slug'] for h in inside['results']] == ['krakow']

    def test_geometry_excluded_from_lists_by_default(self, client):
        hit = client.get('/api/places/').json()['results'][0]
        assert 'geometry' not in hit

    def test_geometry_available_on_request(self, client):
        hit = client.get('/api/places/', {'geometry': 'true'}).json()['results'][0]
        assert hit['geometry']['type'] == 'MultiPolygon'

    def test_detail_includes_geometry(self, client):
        body = client.get('/api/places/krakow/').json()
        assert body['slug'] == 'krakow'
        assert body['geometry']['type'] == 'MultiPolygon'

    def test_detail_404(self, client):
        assert client.get('/api/places/nope/').status_code == 404

    def test_detail_unknown_type_404(self, client):
        assert client.get('/api/villages/krakow/').status_code == 404

    def test_paging(self, client):
        body = client.get('/api/places/', {'page_size': '1'}).json()
        assert len(body['results']) == 1
        assert body['total'] == 2
        assert body['page_size'] == 1

    def test_bad_paging_is_a_400(self, client):
        assert client.get('/api/places/', {'page_size': '0'}).status_code == 400


class TestCrossTypeRanking:
    """
    Scores from different indexes are only comparable when term statistics are
    gathered globally. Without that, a search for a town returns the streets
    named after it and not the town, because the streets index is much larger
    and computes a different IDF for the same term.
    """

    @pytest.fixture(autouse=True)
    def indexed(self, prefix, opensearch, settings, db):
        settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = prefix
        settings.ROOT_URL = 'testserver'

        Place.objects.create(name='Peel', border=MultiPolygon(square(1, 1)))
        # the same name recurring across many street rows is what skews IDF
        for i in range(40):
            Street.objects.create(
                name='Peel Road %d' % i,
                lines=MultiLineString(LineString((i / 100, 0), (i / 100 + 0.01, 0.01))),
            )
        for name in ('places', 'streets'):
            reindex(name, prefix, opensearch)

    def test_the_town_outranks_the_streets_named_after_it(self, client):
        results = client.get('/api/search/', {'q': 'peel'}).json()['results']
        assert results, 'no results at all'
        assert results[0]['type'] == 'places', \
            'expected the town first, got: {}'.format(
                [(h['name'], h['type']) for h in results[:3]])
        assert results[0]['name'] == 'Peel'

    def test_single_type_search_still_works(self, client):
        """Only one index, so global statistics are unnecessary there."""
        body = client.get('/api/streets/', {'q': 'peel'}).json()
        assert body['total'] == 40

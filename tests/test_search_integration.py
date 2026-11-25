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
    # seeded by migration 0031; take it rather than creating a duplicate
    poland, _ = Country.objects.get_or_create(
        iso2='PL', iso3='POL', un=616,
        defaults={'name': 'Polska', 'slug': 'polska'},
    )
    poland.border = MultiPolygon(square(0, 0, 10))
    poland.save()
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


class TestEveryWordCounts:
    """
    "krakow, kazimierz": the district has one word in its name and the other
    in its ancestry, and lost to everything that had just one of them.
    """

    @pytest.fixture(autouse=True)
    def indexed(self, prefix, opensearch, settings, db):
        from django_osm_locations.models import CountrySubarea, PlaceSubarea

        settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = prefix
        settings.ROOT_URL = 'testserver'
        CountrySubarea.objects.create(name='powiat krakowski', border=MultiPolygon(square(0, 6)),
                                      area=36.0)
        Place.objects.create(name='Kraków', border=MultiPolygon(square(1, 2)), area=4.0)
        Place.objects.create(name='Kazimierz Dolny', border=MultiPolygon(square(7, 1)), area=1.0)
        PlaceSubarea.objects.create(name='Kazimierz', border=MultiPolygon(square(1.5, 0.2)),
                                    area=0.04, place=[['Kraków', 'krakow']])
        for name in ('country-subareas', 'places', 'place-subareas'):
            reindex(name, prefix, opensearch)

    def test_the_district_with_both_words_comes_first(self, client):
        results = client.get('/api/search/', {'q': 'Krakow, Kazimierz'}).json()['results']
        assert [h['name'] for h in results] == ['Kazimierz']

    def test_a_word_nothing_has_still_finds_the_rest(self, client):
        """A typo must not empty the answer: it falls back to any word."""
        results = client.get('/api/search/', {'q': 'Krakow Xqzw'}).json()['results']
        assert 'Kraków' in [h['name'] for h in results]


class TestTypos:
    """
    "Warszwa" found nothing, and "Szczecn" the village Szczecno: a misspelt
    name is the start of no name, or of some other.
    """

    @pytest.fixture(autouse=True)
    def indexed(self, prefix, opensearch, settings, db):
        settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = prefix
        settings.ROOT_URL = 'testserver'
        Place.objects.create(name='Warszawa', border=MultiPolygon(square(1, 2)), area=4.0,
                             population=1800000)
        Place.objects.create(name='Szczecin', border=MultiPolygon(square(4, 2)), area=4.0,
                             population=400000)
        Place.objects.create(name='Szczecno', border=MultiPolygon(square(7, 1)), area=0.1,
                             population=700)
        Street.objects.create(
            name='Marszałkowska',
            lines=MultiLineString(LineString((1.1, 2.1), (1.2, 2.2))),
            place=[['Warszawa', 'warszawa']],
        )
        for name in ('places', 'streets'):
            reindex(name, prefix, opensearch)

    def names(self, client, q, **params):
        found = client.get('/api/search/', dict(params, q=q)).json()
        return [h['name'] for h in found['results']]

    @pytest.mark.parametrize('q', ['Warszwa', 'Warzsawa', 'Warsawa', 'warszaw'])
    def test_a_misspelt_name_is_found(self, client, q):
        assert self.names(client, q)[0] == 'Warszawa'

    def test_with_another_word_as_typed(self, client):
        assert self.names(client, 'Marszałkowska Warszwa') == ['Marszałkowska']

    def test_a_typo_that_is_the_start_of_another_name_finds_both(self, client):
        assert set(self.names(client, 'Szczecn')) == {'Szczecno', 'Szczecin'}

    def test_a_name_typed_right_is_not_joined_by_its_neighbours(self, client):
        """Szczecno is a village, not Szczecin misspelt."""
        assert self.names(client, 'Szczecno') == ['Szczecno']

    def test_the_total_counts_them(self, client):
        found = client.get('/api/search/', {'q': 'Szczecn'}).json()
        assert found['total'] == 2

    def test_on_every_page(self, client):
        first = self.names(client, 'Szczecn', page_size=1)
        second = self.names(client, 'Szczecn', page_size=1, page=2)
        assert sorted(first + second) == ['Szczecin', 'Szczecno']


class TestWordsANameLeavesOut:
    """
    Osiedle, Aleja, Plac: required while something has them, and not after.
    Never required, "Plac Nowy, Kraków" was the village Nowy Kraków; always
    required, "Osiedle Zgody" missed the street mapped as Zgody.
    """

    @pytest.fixture(autouse=True)
    def indexed(self, prefix, opensearch, settings, db):
        settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = prefix
        settings.ROOT_URL = 'testserver'
        Place.objects.create(name='Kraków', border=MultiPolygon(square(1, 2)), area=4.0,
                             population=800000)
        Place.objects.create(name='Nowy Kraków', border=MultiPolygon(square(7, 1)), area=0.1)
        for name, x in (('Plac Nowy', 1.1), ('Zgody', 1.3)):
            Street.objects.create(
                name=name,
                lines=MultiLineString(LineString((x, 2.1), (x + 0.1, 2.2))),
                place=[['Kraków', 'krakow']],
            )
        for name in ('places', 'streets'):
            reindex(name, prefix, opensearch)

    def names(self, client, q):
        return [h['name'] for h in client.get('/api/search/', {'q': q}).json()['results']]

    @pytest.mark.parametrize('q', ['Plac Nowy Kraków', 'pl. Nowy Kraków'])
    def test_what_has_the_word_comes_alone(self, client, q):
        assert self.names(client, q) == ['Plac Nowy']

    @pytest.mark.parametrize('q', ['Osiedle Zgody Kraków', 'os. Zgody Kraków'])
    def test_and_what_leaves_it_out_is_found_when_nothing_has_it(self, client, q):
        assert self.names(client, q) == ['Zgody']


class TestPreferAndFields:
    """Two streets of one name, and where the person asking is."""

    @pytest.fixture(autouse=True)
    def indexed(self, prefix, opensearch, settings, db):
        settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = prefix
        settings.ROOT_URL = 'testserver'
        poland, _ = Country.objects.get_or_create(
            iso2='PL', iso3='POL', un=616, defaults={'name': 'Polska', 'slug': 'polska'})
        poland.border = MultiPolygon(square(0, 0, 10))
        poland.save()
        Place.objects.create(name='Kraków', border=MultiPolygon(square(1, 1)), area=4.0,
                             population=800000)
        Place.objects.create(name='Gdańsk', border=MultiPolygon(square(8, 8)), area=4.0,
                             population=480000)
        Place.objects.create(name='Gdańsk Mały', border=MultiPolygon(square(1, 3)), area=0.1,
                             population=90)
        for town, slug, at in (('Kraków', 'krakow', 1.1), ('Gdańsk', 'gdansk', 8.1)):
            Street.objects.create(
                name='Długa',
                lines=MultiLineString(LineString((at, at), (at + 0.1, at + 0.1))),
                place=[[town, slug]],
            )
        for name in ('countries', 'places', 'streets'):
            reindex(name, prefix, opensearch)

    def found(self, client, **params):
        return client.get('/api/search/', params).json()['results']

    @pytest.mark.parametrize('at,town', [('1.15,1.15', 'Kraków'), ('8.15,8.15', 'Gdańsk')])
    def test_the_nearer_of_two_streets_comes_first(self, client, at, town):
        streets = self.found(client, q='Długa', type='streets', prefer=at)
        assert [s['path'][-2] for s in streets] == [town] + [t for t in ('Kraków', 'Gdańsk') if t != town]

    def test_the_far_one_is_still_found(self, client):
        assert len(self.found(client, q='Długa', type='streets', prefer='1.15,1.15')) == 2

    def test_a_city_typed_far_from_it_is_still_the_city(self, client):
        """Asked from beside Gdańsk Mały, Gdańsk is the city of 480,000."""
        assert self.found(client, q='Gdańsk', prefer='1.2,3.2')[0]['name'] == 'Gdańsk'

    def test_a_mistake_in_it_is_a_400(self, client):
        assert client.get('/api/search/', {'q': 'Długa', 'prefer': 'here'}).status_code == 400

    def test_suggest_fields(self, client):
        street = self.found(client, q='Długa Kraków', type='streets', fields='suggest')[0]
        assert list(street) == ['type', 'slug', 'name', 'path', 'country', 'center', 'bbox', 'score']
        assert street['path'][-2:] == ['Kraków', 'Długa']
        whole = self.found(client, q='Długa Kraków', type='streets')[0]
        assert all(street[name] == whole[name] for name in ('type', 'slug', 'name', 'path', 'center'))

    def test_fields_named(self, client):
        whole = self.found(client, q='Długa Kraków', type='streets')[0]
        assert self.found(client, q='Długa Kraków', type='streets', fields='name,slug') == [
            {'name': 'Długa', 'slug': whole['slug']}]

    def test_a_typo_is_found_with_fewer_fields_as_with_all(self, client):
        assert [h['name'] for h in self.found(client, q='Gdansk Maly', fields='name')] == \
               [h['name'] for h in self.found(client, q='Gdansk Maly')]


class TestTheSizeOfThePlaceItIsIn:
    """
    "Marszałkowska" was Warszawa's 13th, behind twelve of the name that
    scored the same, and "Nowa Huta" the district of Kraków 9th, behind
    villages: a street and a district rank by their place's people.
    """

    @pytest.fixture(autouse=True)
    def indexed(self, prefix, opensearch, settings, db):
        from django_osm_locations.models import PlaceSubarea

        settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = prefix
        settings.ROOT_URL = 'testserver'
        towns = [('Zawiercie', 49000, 1), ('Warszawa', 1863845, 3), ('Pcim', None, 5), ('Kraków', 800000, 7)]
        for town, people, at in towns:
            place = Place.objects.create(name=town, border=MultiPolygon(square(at, at)), area=4.0,
                                         population=people)
            Street.objects.create(
                name='Marszałkowska',
                lines=MultiLineString(LineString((at + 0.1, at + 0.1), (at + 0.2, at + 0.2))),
                place=[[town, place.slug]],
            )
        Place.objects.create(name='Nowa Huta', border=MultiPolygon(square(5, 1)), area=0.5,
                             population=300)
        PlaceSubarea.objects.create(name='Nowa Huta', border=MultiPolygon(square(7.2, 7.2, 0.5)),
                                    area=0.25, place=[['Kraków', 'krakow']])
        for name in ('places', 'place-subareas', 'streets'):
            reindex(name, prefix, opensearch)

    def found(self, client, **params):
        return client.get('/api/search/', params).json()['results']

    def test_the_street_of_the_largest_town_comes_first(self, client):
        streets = self.found(client, q='Marszałkowska', type='streets')
        assert [s['path'][-2] for s in streets] == ['Warszawa', 'Kraków', 'Zawiercie', 'Pcim']

    def test_it_is_in_the_document(self, client):
        streets = self.found(client, q='Marszałkowska Warszawa', type='streets')
        assert streets[0]['place_population'] == 1863845

    def test_the_district_of_a_city_before_the_village_of_its_name(self, client):
        found = self.found(client, q='Nowa Huta')
        assert (found[0]['type'], found[0]['path'][-2:]) == ('place-subareas', ['Kraków', 'Nowa Huta'])
        assert found[1]['type'] == 'places'

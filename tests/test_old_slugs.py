"""
Following a slug of before, ``<name>-<OSM id>``, to the one the location has now.

Every duplicate but the biggest used to get its OSM id --
``slug || '-' || COALESCE(relation_osm_id, way_osm_id, node_osm_id, id)``.

Needs the OpenSearch container from docker-compose.test.yml:

    docker compose -f docker-compose.test.yml up -d
    pytest -m search
"""
import uuid

import pytest
from django.contrib.gis.geos import LineString, MultiLineString, MultiPolygon, Point
from django.core.cache import cache

from django_osm_locations.conf import OLD_SLUG_MIDDLEWARE
from django_osm_locations.models import Place, PlaceSubarea, Street
from django_osm_locations.search.indexer import Reindexer
from django_osm_locations.search.indexes import INDEXES
from django_osm_locations.search.old_slugs import resolve_old_slug

from .conftest import square

pytestmark = [pytest.mark.search, pytest.mark.django_db]

pytest.importorskip('opensearchpy', reason='needs opensearch-py')

POLSKA = ['Polska', 'polska']
KRAKOW = ['Kraków', 'krakow']
WOLA = ['Wola', 'wola']
SUBDOMAIN = 'django_osm_locations.middleware.PlaceSubdomainMiddleware'


def street(name, slug, way, place, x=20.0):
    return Street.objects.create(name=name, slug=slug, way_osm_id=way, country=[POLSKA],
                                 place=[place],
                                 lines=MultiLineString(LineString((x, 50.0), (x + 0.001, 50.0))))


@pytest.fixture
def indexed(settings):
    from django_osm_locations.search.client import get_client

    client = get_client()
    try:
        client.info()
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip('OpenSearch is not reachable: {}'.format(exc))
    prefix = 'test-old-slugs-{}'.format(uuid.uuid4().hex[:8])

    Place.objects.create(name='Kraków', slug='krakow', relation_osm_id=130919,
                         border=MultiPolygon(square(19.8, 49.9, 0.3)), country=[POLSKA])
    Place.objects.create(name='Wieliczka', slug='wieliczka', node_osm_id=777,
                         center=Point(20.06, 49.98), country=[POLSKA])
    Place.objects.create(name='Müncheberg', slug='muncheberg', node_osm_id=999,
                         center=Point(14.13, 52.5), country=[['Deutschland', 'deutschland']])
    street('Floriańska', 'florianska-krakow', 25986049, KRAKOW)
    keeper = street('Polna', 'polna-wola', 4000, WOLA)
    street('Polna', 'polna-wola-4001', 4001, WOLA, x=20.01).mark_as_merged(into=keeper)
    street('Polna', 'polna', 777, ['Niepołomice', 'niepolomice'], x=20.2)
    street('Aleja 3 Maja', 'aleja-3-maja', 3, KRAKOW)
    street('Lipowa', 'lipowa-wola', 5000, WOLA)
    PlaceSubarea.objects.create(name='Grodzisko', slug='grodzisko', way_osm_id=6000,
                                border=MultiPolygon(square(19.95, 50.0, 0.01)),
                                country=[POLSKA], place=[KRAKOW])
    # a street and an estate of one name in Wola: the name alone cannot say which
    street('Leśna', 'lesna-wola', 7000, WOLA, x=20.03)
    PlaceSubarea.objects.create(name='Leśna', slug='lesna-wola-osiedle', way_osm_id=7001,
                                border=MultiPolygon(square(20.03, 50.0, 0.01)),
                                country=[POLSKA], place=[WOLA])
    Place.objects.create(name='Wola', slug='wola', node_osm_id=8000,
                         center=Point(20.0, 50.0), country=[POLSKA])
    for name in ('places', 'place-subareas', 'streets'):
        reindexer = Reindexer(INDEXES[name], client=client, prefix=prefix)
        reindexer.create()
        reindexer.populate()
        reindexer.promote()
    settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = prefix
    settings.ROOT_URL = 'testserver'
    cache.clear()
    yield client
    cache.clear()
    client.indices.delete(index='{}-*'.format(prefix), ignore_unavailable=True)


class TestTheFunction:
    def test_by_its_own_way_id(self, indexed):
        assert resolve_old_slug('florianska-25986049') == 'florianska-krakow'

    def test_by_the_id_of_a_piece_merged_into_it(self, indexed):
        assert resolve_old_slug('polna-4001') == 'polna-wola'

    def test_a_node_and_a_way_numbered_alike_are_told_apart_by_the_name(self, indexed):
        """OSM numbers nodes, ways and relations each from one."""
        assert resolve_old_slug('wieliczka-777') == 'wieliczka'
        assert resolve_old_slug('polna-777') == 'polna'

    def test_the_name_has_to_be_the_locations(self, indexed):
        assert resolve_old_slug('grodzka-25986049') is None

    def test_a_name_transliterated_the_old_way(self, indexed):
        """German umlauts were ue, oe, ae."""
        assert resolve_old_slug('muencheberg-999') == 'muncheberg'

    def test_a_slug_of_today_is_left_alone(self, indexed):
        """aleja-3-maja ends in a number, and is the street's slug now."""
        assert resolve_old_slug('aleja-3-maja') is None

    def test_not_an_old_slug_at_all(self, indexed):
        assert resolve_old_slug('florianska') is None
        assert resolve_old_slug('') is None

    def test_a_number_too_big_for_an_id_is_no_error(self, indexed):
        assert resolve_old_slug('florianska-99999999999999999999') is None

    def test_limited_to_a_type(self, indexed):
        assert resolve_old_slug('florianska-25986049', types=['places']) is None

    def test_a_number_gone_from_osm_by_the_name_under_the_place(self, indexed):
        assert resolve_old_slug('lipowa-123', within='wola') == 'lipowa-wola'

    def test_but_only_under_a_place(self, indexed):
        assert resolve_old_slug('lipowa-123') is None

    def test_of_any_type(self, indexed):
        assert resolve_old_slug('grodzisko-1', within='krakow') == 'grodzisko'

    def test_not_the_namesake_under_another_place(self, indexed):
        """polna is in Niepołomice; Wola's Polna is polna-wola."""
        assert resolve_old_slug('polna-1', within='wola') == 'polna-wola'

    def test_and_only_when_one_location_fits(self, indexed):
        assert resolve_old_slug('lesna-1', within='wola') is None

    def test_the_answer_is_kept(self, indexed):
        assert resolve_old_slug('florianska-25986049') == 'florianska-krakow'
        assert resolve_old_slug('florianska-25986049', client=Unreachable()) == 'florianska-krakow'

    def test_and_so_is_no_answer(self, indexed):
        assert resolve_old_slug('grodzka-25986049') is None
        assert resolve_old_slug('grodzka-25986049', client=Unreachable()) is None


class Unreachable:
    def search(self, *args, **kwargs):
        raise AssertionError('asked OpenSearch again')


@pytest.fixture
def redirects(indexed, settings):
    settings.MIDDLEWARE = [OLD_SLUG_MIDDLEWARE, SUBDOMAIN]


class TestTheMiddleware:
    def test_an_old_slug_in_the_api(self, redirects, client):
        response = client.get('/api/streets/florianska-25986049/')
        assert response.status_code == 301
        assert response['Location'] == '/api/streets/florianska-krakow/'

    def test_the_query_string_goes_along(self, redirects, client):
        response = client.get('/api/streets/florianska-25986049/', {'lang': 'en'})
        assert response['Location'] == '/api/streets/florianska-krakow/?lang=en'

    def test_any_segment_of_any_path(self, redirects, client):
        """The project's own URLs, which it knows and this does not."""
        response = client.get('/miasto/krakow/ulica/florianska-25986049/zdjecia/')
        assert response['Location'] == '/miasto/krakow/ulica/florianska-krakow/zdjecia/'

    def test_a_404_with_nothing_old_in_it_stays_a_404(self, redirects, client):
        assert client.get('/api/streets/no-such-street/').status_code == 404

    def test_a_page_that_is_found_is_left_alone(self, redirects, client):
        assert client.get('/api/streets/florianska-krakow/').status_code == 200

    def test_without_it_nothing_is_redirected(self, indexed, client):
        assert client.get('/api/streets/florianska-25986049/').status_code == 404

    def test_an_old_place_slug_as_a_subdomain(self, redirects, client):
        response = client.get('/api/streets/', HTTP_HOST='wieliczka-777.testserver')
        assert response.status_code == 301
        assert response['Location'] == 'http://wieliczka.testserver/api/streets/'

    def test_the_subdomain_narrows_the_name_alone(self, redirects, client):
        response = client.get('/ulica/lipowa-123/', HTTP_HOST='wola.testserver')
        assert response['Location'] == '/ulica/lipowa-wola/'

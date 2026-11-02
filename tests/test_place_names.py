"""
The city typed is the city, not the region named after it.

In Spain and Italy a city is often called what the region or province round
it is -- Comunidad de Madrid, the province of Palermo -- so the city's name
is a word of every suburb's ancestry too, and a suburb's house, with a
shorter ancestry, came first: "Calle de Granada 28, Madrid" was the one in
Leganés, "Corso Umberto I 62, Palermo" the one in Trabia.

Needs the OpenSearch container from docker-compose.test.yml:

    docker compose -f docker-compose.test.yml up -d
    pytest -m search
"""
import uuid

import pytest
from django.contrib.gis.geos import LineString, MultiLineString, Point
from django.db import transaction

from django_osm_locations.management.commands.importers.base import BaseImporter
from django_osm_locations.models import Address, Street
from django_osm_locations.search.indexer import Reindexer
from django_osm_locations.search.indexes import INDEXES

pytestmark = [pytest.mark.search, pytest.mark.django_db]

pytest.importorskip('opensearchpy', reason='needs opensearch-py')

ESPANA = ['España', 'espana']
MADRID_REGION = ['Comunidad de Madrid', 'comunidad-de-madrid']
ITALIA = ['Italia', 'italia']
SICILIA = ['Sicilia', 'sicilia']
PALERMO_PROVINCE = ['Palermo', 'palermo-provincia']

# the suburbs first, so that a tie goes their way, as it did in the packs
HOUSES = [
    ('Calle Granada', 'calle-granada-leganes', '28', (-3.76, 40.33),
     {'country': [ESPANA], 'countrysubarea': [MADRID_REGION], 'place': [['Leganés', 'leganes']]}),
    ('Calle de Granada', 'calle-de-granada-madrid', '28', (-3.67, 40.40),
     {'country': [ESPANA], 'countrysubarea': [MADRID_REGION], 'place': [['Madrid', 'madrid']],
      'placesubarea': [['Retiro', 'retiro'], ['Pacífico', 'pacifico']]}),
    ('Corso Umberto I', 'corso-umberto-i-trabia', '62', (13.65, 37.99),
     {'country': [ITALIA], 'countrysubarea': [SICILIA, PALERMO_PROVINCE],
      'place': [['Trabia', 'trabia']]}),
    ('Corso Umberto I', 'corso-umberto-i-palermo', '62', (13.36, 38.12),
     {'country': [ITALIA], 'countrysubarea': [SICILIA, PALERMO_PROVINCE],
      'place': [['Palermo', 'palermo']], 'placesubarea': [['Kalsa', 'kalsa'], ['Tribunali', 'tribunali']]}),
]


@pytest.fixture(scope='module')
def prefix(django_db_setup, django_db_blocker):
    from django_osm_locations.search.client import get_client

    client = get_client()
    try:
        client.info()
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip('OpenSearch is not reachable: {}'.format(exc))
    value = 'test-places-{}'.format(uuid.uuid4().hex[:8])
    with django_db_blocker.unblock():
        with transaction.atomic():
            for name, slug, number, (x, y), ancestry in HOUSES:
                street = Street.objects.create(
                    name=name, slug=slug, lines=MultiLineString(LineString((x, y), (x + 0.002, y))),
                    **ancestry)
                Address.objects.create(
                    name='{} {}'.format(name, number), house_number=number,
                    house_number_sort=BaseImporter._house_number_sort_key(number),
                    street_name=name, center=Point(x + 0.001, y), street=[[name, slug]], **ancestry)
            # the rest of each region, which makes its name a word every house has
            for region, places, (x, y) in (
                    ({'country': [ESPANA], 'countrysubarea': [MADRID_REGION]},
                     ['Getafe', 'Móstoles', 'Alcorcón', 'Fuenlabrada', 'Parla'], (-3.8, 40.3)),
                    ({'country': [ITALIA], 'countrysubarea': [SICILIA, PALERMO_PROVINCE]},
                     ['Bagheria', 'Monreale', 'Carini', 'Partinico', 'Termini Imerese'], (13.4, 38.0))):
                for i, place in enumerate(places):
                    ancestry = dict(region, place=[[place, place.lower()]])
                    for n in range(8):
                        Address.objects.create(
                            name='Mayor {}'.format(n + 1), house_number=str(n + 1), street_name='Mayor',
                            center=Point(x + i * 0.01, y + n * 0.001), **ancestry)
            for name in ('streets', 'addresses'):
                reindexer = Reindexer(INDEXES[name], client=client, prefix=value)
                reindexer.create()
                reindexer.populate()
                reindexer.promote()
            transaction.set_rollback(True)
    yield value
    client.indices.delete(index='{}-*'.format(value), ignore_unavailable=True)


@pytest.fixture(autouse=True)
def api(prefix, settings):
    settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = prefix
    settings.ROOT_URL = 'testserver'


def first(client, q):
    response = client.get('/api/search/', {'q': q})
    assert response.status_code == 200, response.content
    return response.json()['results'][0]


class TestTheCityNotItsRegion:
    @pytest.mark.parametrize('q,city', [
        ('Calle de Granada 28, Madrid', 'madrid'),
        ('Madrid, Calle de Granada 28', 'madrid'),
        ('Corso Umberto I 62, Palermo', 'palermo'),
        ('corso umberto i 62 palermo', 'palermo'),
    ])
    def test_the_house_in_the_city(self, client, q, city):
        hit = first(client, q)
        assert hit['type'] == 'addresses' and city in hit['ancestry_slugs'], hit['path']

    @pytest.mark.parametrize('q,city', [
        ('Calle Granada 28, Leganés', 'leganes'),
        ('Corso Umberto I 62, Trabia', 'trabia'),
    ])
    def test_and_the_suburb_by_its_own_name(self, client, q, city):
        assert city in first(client, q)['ancestry_slugs']

    def test_the_street_in_the_city(self, client):
        hit = first(client, 'Corso Umberto I Palermo')
        assert (hit['type'], hit['slug']) == ('streets', 'corso-umberto-i-palermo')

"""
The whole API over a small Poland: PostGIS -> reindex -> alias -> HTTP.

Built the way an import leaves it -- voivodeships at admin_level 4, Kraków
both a city county and a place, districts, streets, and house numbers on them
-- and asked what a person in Poland would ask, with and without the Polish
letters. The house numbers are the ones that came out wrong against the
Poland pack: Gajcego 140-148 and 146A in Łódź were indexed before 14, and
"14" found them all equally.

Needs the OpenSearch container from docker-compose.test.yml, so it is opt-in:

    docker compose -f docker-compose.test.yml up -d
    pytest -m search
"""
import math
import uuid

import pytest
from django.contrib.gis.geos import LineString, MultiLineString, MultiPolygon, Point
from django.db import transaction

from django_osm_locations.management.commands.importers.base import BaseImporter
from django_osm_locations.models import (
    Address,
    Country,
    CountrySubarea,
    Place,
    PlaceSubarea,
    Street,
)
from django_osm_locations.search.indexer import Reindexer
from django_osm_locations.search.indexes import INDEXES

from .conftest import square

pytestmark = [pytest.mark.search, pytest.mark.django_db]

pytest.importorskip('opensearchpy', reason='needs opensearch-py')

POLSKA = ['Polska', 'polska']
MALOPOLSKIE = ['województwo małopolskie', 'wojewodztwo-malopolskie']
LODZKIE = ['województwo łódzkie', 'wojewodztwo-lodzkie']
# a city with county rights is a county as well as a place, both called Kraków
KRAKOW_COUNTY = ['Kraków', 'krakow']
KRAKOW = ['Kraków', 'krakow']
LODZ_COUNTY = ['Łódź', 'lodz']
LODZ = ['Łódź', 'lodz']
STARE_MIASTO = ['Stare Miasto', 'stare-miasto']
KAZIMIERZ = ['Kazimierz', 'kazimierz']
WIDZEW = ['Widzew', 'widzew']

IN_KRAKOW = {'country': [POLSKA], 'countrysubarea': [MALOPOLSKIE, KRAKOW_COUNTY],
             'place': [KRAKOW]}
IN_LODZ = {'country': [POLSKA], 'countrysubarea': [LODZKIE, LODZ_COUNTY], 'place': [LODZ]}
IN_LUBLINIEC = {'country': [POLSKA],
                'countrysubarea': [['województwo śląskie', 'wojewodztwo-slaskie'],
                                   ['powiat lubliniecki', 'powiat-lubliniecki']],
                'place': [['Lubliniec', 'lubliniec']]}
# with a district, as the city's streets have, and Lubliniec's do not: a
# longer ancestry, and every word of it worth less
IN_LUBLIN = {'country': [POLSKA],
             'countrysubarea': [['województwo lubelskie', 'wojewodztwo-lubelskie'], ['Lublin', 'lublin']],
             'place': [['Lublin', 'lublin']], 'placesubarea': [['Konstantynów', 'konstantynow']]}
MAZOWIECKIE = ['województwo mazowieckie', 'wojewodztwo-mazowieckie']
# what Warszawa is called in other languages, beside English and German: in
# OSM it has some 200 such names
WARSZAWA_ELSEWHERE = {
    'fr': 'Varsovie', 'es': 'Varsovia', 'it': 'Varsavia', 'pt': 'Varsóvia', 'nl': 'Warschau',
    'cs': 'Varšava', 'sk': 'Varšava', 'uk': 'Варшава', 'ru': 'Варшава', 'be': 'Варшава',
    'lt': 'Varšuva', 'lv': 'Varšava', 'hu': 'Varsó', 'fi': 'Varsova', 'sv': 'Warszawa',
    'tr': 'Varşova', 'el': 'Βαρσοβία', 'he': 'ורשה', 'ar': 'وارسو', 'ja': 'ワルシャワ',
    'zh': '华沙', 'ko': '바르샤바', 'eo': 'Varsovio', 'la': 'Varsavia', 'ga': 'Vársá',
    'cy': 'Warsaw', 'is': 'Varsjá', 'et': 'Varssavi', 'sl': 'Varšava', 'hr': 'Varšava',
}

# Floriańska 14 in Kraków, where `near` is asked: 130 m from the nearest of
# its neighbours, which are 20 m apart along the street
FLORIANSKA_14 = (19.9420, 50.0645)


@pytest.fixture(scope='module')
def opensearch():
    from django_osm_locations.search.client import get_client

    client = get_client()
    try:
        client.info()
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip('OpenSearch is not reachable: {}'.format(exc))
    return client


def line(lon, lat, length=0.004):
    return MultiLineString(LineString((lon, lat), (lon + length, lat)))


def address(street, ancestry, number, lon, lat, postcode=''):
    """An address the way the importer writes one."""
    return Address.objects.create(
        name='{} {}'.format(street.name, number),
        house_number=number,
        house_number_sort=BaseImporter._house_number_sort_key(number),
        street_name=street.name,
        settlement_name=ancestry['place'][0][0],
        postcode=postcode,
        center=Point(lon, lat),
        street=[[street.name, street.slug]],
        **ancestry,
    )


def build_poland():
    poland = Country.objects.get(iso2='PL')
    poland.border = MultiPolygon(square(14.0, 49.0, 10.0))
    poland.names = {'en': 'Poland', 'de': 'Polen', 'cs': 'Polsko'}
    poland.save()

    for name, slug, box, area, names in [
        (MALOPOLSKIE[0], MALOPOLSKIE[1], square(19.0, 49.2, 2.0), 15183.0,
         {'en': 'Lesser Poland Voivodeship'}),
        (LODZKIE[0], LODZKIE[1], square(18.0, 51.0, 2.0), 18219.0,
         {'en': 'Łódź Voivodeship'}),
    ]:
        CountrySubarea.objects.create(name=name, slug=slug, border=MultiPolygon(box),
                                      admin_level=4, area=area, names=names,
                                      country=[POLSKA])
    CountrySubarea.objects.create(name='powiat krakowski', border=MultiPolygon(square(19.5, 49.9, 0.2)),
                                  admin_level=6, area=1230.0,
                                  country=[POLSKA], countrysubarea=[MALOPOLSKIE])
    CountrySubarea.objects.create(name='Kraków', slug='krakow', border=MultiPolygon(square(19.79, 49.97, 0.3)),
                                  admin_level=6, area=327.0, names={'en': 'Cracow'},
                                  country=[POLSKA], countrysubarea=[MALOPOLSKIE])
    CountrySubarea.objects.create(name='Łódź', slug='lodz', border=MultiPolygon(square(19.32, 51.68, 0.3)),
                                  admin_level=6, area=293.0, names={'en': 'Lodz'},
                                  country=[POLSKA], countrysubarea=[LODZKIE])

    Place.objects.create(name='Kraków', border=MultiPolygon(square(19.79, 49.97, 0.3)),
                         population=804237, area=327.0, admin_level=8,
                         names={'en': 'Cracow', 'de': 'Krakau'},
                         country=[POLSKA], countrysubarea=[MALOPOLSKIE, KRAKOW_COUNTY])
    Place.objects.create(name='Łódź', border=MultiPolygon(square(19.32, 51.68, 0.3)),
                         population=658444, area=293.0, admin_level=8, names={'en': 'Lodz'},
                         country=[POLSKA], countrysubarea=[LODZKIE, LODZ_COUNTY])
    Place.objects.create(name='Krakuszowice', border=MultiPolygon(square(20.30, 49.95, 0.02)),
                         population=400, area=4.0,
                         country=[POLSKA], countrysubarea=[MALOPOLSKIE])
    # Lipno is a town in Poland and a municipality in Czechia: one slug, two
    # countries, which only loading both packs brings together
    Place.objects.create(name='Lipno', slug='lipno', border=MultiPolygon(square(19.17, 52.84, 0.05)),
                         population=14000, area=11.0,
                         country=[POLSKA], countrysubarea=[['województwo kujawsko-pomorskie',
                                                            'wojewodztwo-kujawsko-pomorskie']])
    Place.objects.create(name='Lipno', slug='lipno', border=MultiPolygon(square(13.98, 48.63, 0.05)),
                         population=600, area=15.0,
                         country=[['Česko', 'cesko']], countrysubarea=[['Jihočeský kraj',
                                                                        'jihocesky-kraj']])

    # "Lublin" is the start of Lubliniec and of powiat lubliniecki, and the
    # Lubliniec one of each name came first: "Elizy Orzeszkowej 1, Lublin"
    # was house 1 in Lubliniec. Lubliniec is built first, so that a tie goes
    # its way, as it did there.
    for name, box, population, ancestry in [
            ('Lubliniec', square(18.66, 50.65, 0.05), 23000, IN_LUBLINIEC),
            ('Lublin', square(22.50, 51.20, 0.15), 330000, IN_LUBLIN)]:
        Place.objects.create(name=name, border=MultiPolygon(box), population=population,
                             country=ancestry['country'], countrysubarea=ancestry['countrysubarea'])
        x, y = box.centroid.coords
        street = Street.objects.create(name='Elizy Orzeszkowej', lines=line(x, y),
                                       slug='elizy-orzeszkowej-{}'.format(ancestry['place'][0][1]),
                                       length=300.0, **ancestry)
        address(street, ancestry, '1', x, y)
    # "Warsaw" is a whole word of powiat warszawski zachodni's few names and of
    # Warszawa's many, and the county came first; Warszawa is the one called
    # Warsaw.
    CountrySubarea.objects.create(
        name='powiat warszawski zachodni', border=MultiPolygon(square(20.60, 52.15, 0.2)),
        admin_level=6, area=533.0, population=120000, country=[POLSKA],
        countrysubarea=[MAZOWIECKIE],
        names={'en': 'Warsaw West County', 'de': 'Landkreis Warschau-West'})
    Place.objects.create(name='Warszawa', border=MultiPolygon(square(20.85, 52.10, 0.3)),
                         population=1863056, area=517.0, admin_level=8,
                         names=dict(WARSZAWA_ELSEWHERE, en='Warsaw', de='Warschau'),
                         country=[POLSKA], countrysubarea=[MAZOWIECKIE])

    for name, slug, box, ancestry in [
        ('Stare Miasto', STARE_MIASTO[1], square(19.92, 50.05, 0.04), IN_KRAKOW),
        ('Kazimierz', KAZIMIERZ[1], square(19.93, 50.04, 0.02), IN_KRAKOW),
        ('Widzew', WIDZEW[1], square(19.50, 51.75, 0.10), IN_LODZ),
        ('Bałuty', 'baluty', square(19.40, 51.79, 0.10), IN_LODZ),
    ]:
        PlaceSubarea.objects.create(name=name, slug=slug, border=MultiPolygon(box), **ancestry)

    in_stare_miasto = dict(IN_KRAKOW, placesubarea=[STARE_MIASTO])
    in_widzew = dict(IN_LODZ, placesubarea=[WIDZEW])
    florianska = Street.objects.create(name='Floriańska', slug='florianska-krakow',
                                       lines=line(19.9380, 50.0640), length=335.0, **in_stare_miasto)
    grodzka = Street.objects.create(name='Grodzka', slug='grodzka-krakow',
                                    lines=line(19.9370, 50.0560), length=600.0, **in_stare_miasto)
    trzeciego_maja = Street.objects.create(name='Aleja 3 Maja', slug='aleja-3-maja-krakow',
                                           lines=line(19.9100, 50.0580, 0.01), length=1400.0,
                                           **IN_KRAKOW)
    # no number in its name, and a house 3 on it: "3 Maja" is every word of
    # Maja 3, and still asks for the street
    maja = Street.objects.create(name='Księdza Franciszka Maja', slug='ksiedza-franciszka-maja-krakow',
                                 lines=line(19.9500, 50.0100), length=250.0, **IN_KRAKOW)
    gajcego = Street.objects.create(name='Tadeusza Gajcego', slug='tadeusza-gajcego-lodz',
                                    lines=line(19.5600, 51.7500), length=900.0, **in_widzew)
    florianska_lodz = Street.objects.create(name='Floriańska', slug='florianska-lodz',
                                            lines=line(19.4500, 51.7600), length=420.0, **IN_LODZ)

    # In the order the Poland pack has them: the longer numbers first, so a
    # tie between 14 and 142 goes the wrong way, as it did there.
    for i, number in enumerate(['142', '144', '148', '140', '146', '146A', '14']):
        address(gajcego, in_widzew, number, 19.5600 + i * 0.0003, 51.7500)
    for i, number in enumerate(['13', '15', '12', '14a', '12a', '3', '14', '1']):
        lon, lat = FLORIANSKA_14 if number == '14' else (19.9380 + i * 0.0003, 50.0640)
        address(florianska, in_stare_miasto, number, lon, lat, postcode='31-019')
    address(florianska_lodz, IN_LODZ, '14', 19.4510, 51.7600)
    for i, number in enumerate(['52', '52A', '5']):
        address(grodzka, in_stare_miasto, number, 19.9370 + i * 0.0004, 50.0560, postcode='31-006')
    # 50/52 is a corner of two, numbered so: "50/52" is that house, not flat 52 of 50
    for i, number in enumerate(['50', '3', '5', '50/52']):
        address(trzeciego_maja, IN_KRAKOW, number, 19.9100 + i * 0.0005, 50.0580)
    address(maja, IN_KRAKOW, '3', 19.9500, 50.0100)


@pytest.fixture(scope='module')
def prefix(opensearch, django_db_setup, django_db_blocker):
    """
    One small Poland, indexed once for the module.

    The rows are rolled back once they are indexed: the API reads only the
    index, and nothing is left in the test database for other modules.
    """
    value = 'test-pl-{}'.format(uuid.uuid4().hex[:8])
    with django_db_blocker.unblock():
        with transaction.atomic():
            build_poland()
            for name in INDEXES:
                reindexer = Reindexer(INDEXES[name], client=opensearch, prefix=value)
                reindexer.create()
                reindexer.populate()
                reindexer.promote()
            transaction.set_rollback(True)
    yield value
    opensearch.indices.delete(index='{}-*'.format(value), ignore_unavailable=True)


@pytest.fixture(autouse=True)
def api(prefix, settings):
    settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = prefix
    # the test client talks to "testserver"; without this the subdomain
    # middleware treats it as a foreign host and 301s every request away
    settings.ROOT_URL = 'testserver'


def search(client, path='/api/search/', **params):
    response = client.get(path, params)
    assert response.status_code == 200, response.content
    return response.json()


def first(client, q, **params):
    results = search(client, q=q, **params)['results']
    assert results, 'nothing for {!r}'.format(q)
    return results[0]


def names(results):
    return [hit['name'] for hit in results]


class TestCities:
    @pytest.mark.parametrize('q', ['Kraków', 'Krakow', 'krakow', 'KRAKÓW', 'krak'])
    def test_krakow_however_it_is_typed(self, client, q):
        hit = first(client, q, type='places')
        assert (hit['name'], hit['slug']) == ('Kraków', 'krakow')

    @pytest.mark.parametrize('q', ['Łódź', 'Lodz', 'lodz', 'łodz', 'ŁÓDŹ'])
    def test_lodz_however_it_is_typed(self, client, q):
        assert first(client, q, type='places')['name'] == 'Łódź'

    def test_the_city_before_the_village_that_starts_the_same(self, client):
        """"krak" is Kraków and Krakuszowice equally; 800,000 people decide."""
        assert names(search(client, q='krak', type='places')['results'])[:2] == [
            'Kraków', 'Krakuszowice']

    @pytest.mark.parametrize('q', ['Cracow', 'Krakau'])
    def test_by_another_languages_name(self, client, q):
        assert first(client, q, type='places')['name'] == 'Kraków'

    def test_a_voivodeship(self, client):
        hit = first(client, 'małopolskie')
        assert (hit['type'], hit['admin_level']) == ('country-subareas', 4)
        assert hit['name'] == 'województwo małopolskie'

    def test_the_voivodeship_without_the_polish_letters(self, client):
        assert first(client, 'lodzkie')['name'] == 'województwo łódzkie'


class TestDistricts:
    @pytest.mark.parametrize('q', ['Kraków, Kazimierz', 'Kazimierz Kraków', 'krakow kazimierz'])
    def test_a_district_of_a_city(self, client, q):
        hit = first(client, q)
        assert (hit['type'], hit['name']) == ('place-subareas', 'Kazimierz')

    @pytest.mark.parametrize('q', ['Łódź, Bałuty', 'Lodz Baluty'])
    def test_with_and_without_the_polish_letters(self, client, q):
        assert first(client, q)['name'] == 'Bałuty'

    def test_its_path(self, client):
        assert first(client, 'Kraków, Stare Miasto')['path'] == [
            'Polska', 'województwo małopolskie', 'Kraków', 'Stare Miasto']


class TestStreets:
    @pytest.mark.parametrize('q,slug', [
        ('Floriańska Kraków', 'florianska-krakow'),
        ('Florianska Krakow', 'florianska-krakow'),
        ('Floriańska Łódź', 'florianska-lodz'),
        ('florianska lodz', 'florianska-lodz'),
        ('Gajcego Łódź', 'tadeusza-gajcego-lodz'),
        ('Tadeusza Gajcego', 'tadeusza-gajcego-lodz'),
    ])
    def test_a_street_in_its_city(self, client, q, slug):
        hit = first(client, q)
        assert (hit['type'], hit['slug']) == ('streets', slug)

    def test_a_street_before_the_houses_on_it(self, client):
        """Nine Floriańska addresses match "Floriańska" as well as the street does."""
        hit = first(client, 'Floriańska Kraków')
        assert hit['type'] == 'streets'

    def test_a_number_in_the_streets_name_is_no_house_number(self, client):
        """3 Maja is the 3rd of May; asking for the street must not get house 3."""
        hit = first(client, '3 Maja Kraków')
        assert (hit['type'], hit['name']) == ('streets', 'Aleja 3 Maja')

    def test_nor_a_house_on_another_street_that_has_every_word(self, client):
        """Księdza Franciszka Maja 3 is "3 Maja" too, and is not what was asked."""
        results = search(client, q='3 Maja Kraków')['results']
        assert names(results).index('Aleja 3 Maja') < names(results).index(
            'Księdza Franciszka Maja 3')

    def test_its_path_names_the_city_once(self, client):
        """The city county and the place are both Kraków."""
        assert first(client, 'Grodzka Kraków')['path'] == [
            'Polska', 'województwo małopolskie', 'Kraków', 'Stare Miasto', 'Grodzka']


class TestAddresses:
    @pytest.mark.parametrize('q,name,city', [
        ('Tadeusza Gajcego 14, Łódź', 'Tadeusza Gajcego 14', 'lodz'),
        ('Tadeusza Gajcego 14, Lodz', 'Tadeusza Gajcego 14', 'lodz'),
        ('Gajcego 14 Łódź', 'Tadeusza Gajcego 14', 'lodz'),
        ('Łódź, Tadeusza Gajcego 14', 'Tadeusza Gajcego 14', 'lodz'),
        ('14 Gajcego', 'Tadeusza Gajcego 14', 'lodz'),
        ('Floriańska 14, Kraków', 'Floriańska 14', 'krakow'),
        ('Florianska 14 Krakow', 'Floriańska 14', 'krakow'),
        ('Floriańska 14, Łódź', 'Floriańska 14', 'lodz'),
        ('Floriańska 1, Kraków', 'Floriańska 1', 'krakow'),
        ('Floriańska 3 Kraków', 'Floriańska 3', 'krakow'),
        ('Floriańska 12a, Kraków', 'Floriańska 12a', 'krakow'),
        ('Floriańska 12A, Kraków', 'Floriańska 12a', 'krakow'),
        ('Grodzka 52a Kraków', 'Grodzka 52A', 'krakow'),
        ('Grodzka 52 Kraków', 'Grodzka 52', 'krakow'),
        ('Grodzka 5 Kraków', 'Grodzka 5', 'krakow'),
        ('Aleja 3 Maja 5, Kraków', 'Aleja 3 Maja 5', 'krakow'),
        ('3 Maja 50 Kraków', 'Aleja 3 Maja 50', 'krakow'),
        ('3 Maja 3 Kraków', 'Aleja 3 Maja 3', 'krakow'),
    ])
    def test_the_house_asked_for_comes_first(self, client, q, name, city):
        hit = first(client, q)
        assert (hit['type'], hit['name']) == ('addresses', name), \
            [h['name'] for h in search(client, q=q)['results']]
        assert city in hit['ancestry_slugs']

    def test_then_the_same_number_with_a_letter(self, client):
        results = search(client, q='Floriańska 14 Kraków', type='addresses')['results']
        assert names(results)[:2] == ['Floriańska 14', 'Floriańska 14a']

    def test_then_the_longer_numbers(self, client):
        """Gajcego 14 first, and only then 140-148: nothing ranks among them."""
        results = search(client, q='Gajcego 14', type='addresses')['results']
        assert names(results)[0] == 'Tadeusza Gajcego 14'
        assert sorted(names(results)[1:]) == sorted('Tadeusza Gajcego {}'.format(n) for n in
                                                    ['140', '142', '144', '146', '146A', '148'])

    def test_a_typo_in_the_city_still_finds_the_house(self, client):
        """Nothing has every word, so any word will do -- and the number still counts."""
        results = search(client, q='Floriańska 14 Krakoww', type='addresses')['results']
        assert names(results)[:2] == ['Floriańska 14', 'Floriańska 14']

    def test_a_number_too_long_for_a_house_is_no_error(self, client):
        """A phone number in the search box: an integer term beyond 32 bits is a 400."""
        search(client, q='Floriańska 07700900123')

    def test_its_path(self, client):
        assert first(client, 'Floriańska 14, Kraków')['path'] == [
            'Polska', 'województwo małopolskie', 'Kraków', 'Stare Miasto', 'Floriańska',
            'Floriańska 14']

    def test_it_says_what_it_is(self, client):
        hit = first(client, 'Floriańska 12a, Kraków')
        assert (hit['house_number'], hit['street_name'], hit['settlement_name'],
                hit['postcode']) == ('12a', 'Floriańska', 'Kraków', '31-019')


class TestAStreetsHouses:
    def test_in_walking_order(self, client):
        """/api/addresses/ is ordered by number unless a query says otherwise."""
        body = search(client, '/api/addresses/', within='florianska-krakow')
        assert [h['house_number'] for h in body['results']] == [
            '1', '3', '12', '12a', '13', '14', '14a', '15']

    def test_on_the_other_floriańska_only_its_own(self, client):
        body = search(client, '/api/addresses/', within='florianska-lodz')
        assert [h['house_number'] for h in body['results']] == ['14']

    def test_paged(self, client):
        pages = [search(client, '/api/addresses/', within='tadeusza-gajcego-lodz',
                        page_size=3, page=page) for page in (1, 2, 3)]
        assert {p['total'] for p in pages} == {7}
        numbers = [h['house_number'] for p in pages for h in p['results']]
        assert numbers == ['14', '140', '142', '144', '146', '146A', '148']

    def test_past_the_last_page_is_empty_not_an_error(self, client):
        body = search(client, '/api/addresses/', within='tadeusza-gajcego-lodz',
                      page_size=3, page=4)
        assert (body['total'], body['results']) == (7, [])


class TestWholeWords:
    """A city typed in full is that city, not the start of another place's name."""

    def test_the_house_in_lublin_not_lubliniec(self, client):
        hit = first(client, 'Elizy Orzeszkowej 1, Lublin')
        assert (hit['type'], hit['name']) == ('addresses', 'Elizy Orzeszkowej 1')
        assert 'lublin' in hit['ancestry_slugs']

    @pytest.mark.parametrize('q,slug', [
        ('Elizy Orzeszkowej Lublin', 'elizy-orzeszkowej-lublin'),
        ('Lublin, Elizy Orzeszkowej', 'elizy-orzeszkowej-lublin'),
        ('elizy orzeszkowej lubliniec', 'elizy-orzeszkowej-lubliniec'),
    ])
    def test_the_street(self, client, q, slug):
        hit = first(client, q)
        assert (hit['type'], hit['slug']) == ('streets', slug)

    def test_half_a_word_still_finds_both(self, client):
        results = search(client, q='Elizy Orzeszkowej Lubl', type='streets')['results']
        assert {h['slug'] for h in results} == {'elizy-orzeszkowej-lublin',
                                                'elizy-orzeszkowej-lubliniec'}

    @pytest.mark.parametrize('q', ['Warsaw', 'warsaw', 'Warschau'])
    def test_warsaw_is_warszawa_not_the_county_west_of_it(self, client, q):
        hit = first(client, q)
        assert (hit['type'], hit['name']) == ('places', 'Warszawa')

    def test_and_the_county_by_its_own_name(self, client):
        assert first(client, 'Warsaw West')['name'] == 'powiat warszawski zachodni'


class TestSuggestions:
    """A street first, then its number, as a map offers them."""

    def test_while_a_name_is_typed_no_houses(self, client):
        results = search(client, q='Floriańska', exclude_type='addresses')['results']
        assert results and not [h for h in results if h['type'] == 'addresses']
        assert {h['slug'] for h in results if h['type'] == 'streets'} == {
            'florianska-krakow', 'florianska-lodz'}

    def test_nor_any_other_type_left_out(self, client):
        results = search(client, q='Kraków', exclude_type='addresses,streets')['results']
        assert {h['type'] for h in results} <= {'countries', 'country-subareas', 'places',
                                                'place-subareas'}

    def test_without_it_the_houses_are_there(self, client):
        results = search(client, q='Floriańska 14')['results']
        assert results[0]['type'] == 'addresses'

    def test_then_the_number_on_that_street(self, client):
        body = search(client, '/api/addresses/', within='florianska-krakow', q='14')
        assert names(body['results']) == ['Floriańska 14', 'Floriańska 14a']

    def test_every_house_that_starts_so_and_the_one_typed_first(self, client):
        body = search(client, '/api/addresses/', within='florianska-krakow', q='1')
        assert names(body['results'])[0] == 'Floriańska 1'
        assert sorted(h['house_number'] for h in body['results']) == [
            '1', '12', '12a', '13', '14', '14a', '15']

    def test_on_a_typed_endpoint_it_leaves_that_endpoint(self, client):
        """/api/streets/?exclude_type=addresses is still the streets."""
        body = search(client, '/api/streets/', q='Floriańska', exclude_type='addresses')
        assert {h['type'] for h in body['results']} == {'streets'}


class TestPolishHabits:
    """What is written around an address in Poland and is no word of any name."""

    @pytest.mark.parametrize('q,name,city', [
        ('ul. Floriańska 14, Kraków', 'Floriańska 14', 'krakow'),
        ('ul.Floriańska 14 Kraków', 'Floriańska 14', 'krakow'),
        ('Kraków, ul. Grodzka 52', 'Grodzka 52', 'krakow'),
        ('ul. Tadeusza Gajcego 14, Łódź', 'Tadeusza Gajcego 14', 'lodz'),
        ('Floriańska 14, 31-019 Kraków', 'Floriańska 14', 'krakow'),
        ('Grodzka 52A, 31-006 Kraków', 'Grodzka 52A', 'krakow'),
        ('Floriańska 14/3, Kraków', 'Floriańska 14', 'krakow'),
        ('Floriańska 12a/3 Kraków', 'Floriańska 12a', 'krakow'),
        ('Floriańska 14 m. 3, Kraków', 'Floriańska 14', 'krakow'),
        ('Floriańska 14 m 3 Kraków', 'Floriańska 14', 'krakow'),
        ('Floriańska 14 lok. 3 Kraków', 'Floriańska 14', 'krakow'),
        ('ul. Floriańska 14/3, 31-019 Kraków', 'Floriańska 14', 'krakow'),
        ('al. 3 Maja 5, Kraków', 'Aleja 3 Maja 5', 'krakow'),
        ('Al. 3 Maja 50/52 Kraków', 'Aleja 3 Maja 50/52', 'krakow'),
        ('3 Maja 50/2 Kraków', 'Aleja 3 Maja 50', 'krakow'),
    ])
    def test_the_house_asked_for_comes_first(self, client, q, name, city):
        hit = first(client, q)
        assert (hit['type'], hit['name']) == ('addresses', name), \
            [h['name'] for h in search(client, q=q)['results']]
        assert city in hit['ancestry_slugs']

    @pytest.mark.parametrize('q,slug', [
        ('ul. Floriańska, Kraków', 'florianska-krakow'),
        ('ul. Floriańska Łódź', 'florianska-lodz'),
        ('al. 3 Maja Kraków', 'aleja-3-maja-krakow'),
    ])
    def test_and_the_street(self, client, q, slug):
        hit = first(client, q)
        assert (hit['type'], hit['slug']) == ('streets', slug)

    def test_the_postcode_says_which_city(self, client):
        """Floriańska 14 is in Kraków and in Łódź; 31-019 is Kraków's."""
        hit = first(client, 'Floriańska 14, 31-019')
        assert (hit['name'], hit['postcode']) == ('Floriańska 14', '31-019')
        assert 'krakow' in hit['ancestry_slugs']

    def test_a_wrong_postcode_still_finds_the_house(self, client):
        hit = first(client, 'Floriańska 14, 30-001 Kraków')
        assert hit['name'] == 'Floriańska 14' and 'krakow' in hit['ancestry_slugs']

    def test_the_flat_is_not_house_3(self, client):
        results = search(client, q='Floriańska 14 m. 3, Kraków', type='addresses')['results']
        assert 'Floriańska 3' not in names(results)


class TestFilters:
    def test_within_a_city(self, client):
        body = search(client, q='Floriańska 14', within='lodz')
        assert body['results']
        assert all('lodz' in h['ancestry_slugs'] for h in body['results'])

    def test_within_a_voivodeship(self, client):
        body = search(client, '/api/streets/', within='wojewodztwo-malopolskie', page_size=50)
        assert sorted(names(body['results'])) == ['Aleja 3 Maja', 'Floriańska', 'Grodzka',
                                                  'Księdza Franciszka Maja']

    def test_within_two_ancestors_is_both(self, client):
        body = search(client, '/api/addresses/', within='krakow,grodzka-krakow')
        assert [h['house_number'] for h in body['results']] == ['5', '52', '52A']

    def test_by_type(self, client):
        body = search(client, q='Floriańska', type='streets,addresses', page_size=50)
        assert {h['type'] for h in body['results']} == {'streets', 'addresses'}

    def test_voivodeships_by_admin_level(self, client):
        body = search(client, '/api/country-subareas/', admin_level='4')
        assert set(names(body['results'])) == {'województwo łódzkie', 'województwo małopolskie'}

    def test_several_admin_levels(self, client):
        body = search(client, '/api/country-subareas/', admin_level='4,6')
        assert body['total'] == 6

    def test_a_bounding_box(self, client):
        """Łódź and what is in it, and nothing of Kraków."""
        body = search(client, '/api/search/', bbox='19.3,51.65,19.7,52.0', page_size=50)
        assert body['results']
        assert not [h for h in body['results'] if 'krakow' in h['ancestry_slugs']
                    or h['slug'] == 'krakow']

    def test_near_a_point(self, client):
        lon, lat = FLORIANSKA_14
        body = search(client, '/api/addresses/', near='{},{}'.format(lon, lat), radius='20m')
        assert [h['name'] for h in body['results']] == ['Floriańska 14']

    def test_near_with_a_larger_radius(self, client):
        lon, lat = FLORIANSKA_14
        body = search(client, '/api/addresses/', near='{},{}'.format(lon, lat), radius='500m',
                      page_size=50)
        for hit in body['results']:
            assert distance_m(hit['center'], FLORIANSKA_14) <= 500
        assert 'Floriańska 1' in names(body['results'])
        assert 'Floriańska 14' in names(body['results'])
        assert not [h for h in body['results'] if 'lodz' in h['ancestry_slugs']]


def distance_m(center, point):
    lon1, lat1 = math.radians(center['lon']), math.radians(center['lat'])
    lon2, lat2 = math.radians(point[0]), math.radians(point[1])
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))


class TestOrder:
    def test_by_name(self, client):
        body = search(client, '/api/streets/', within='krakow', order='name')
        assert names(body['results']) == ['Aleja 3 Maja', 'Floriańska', 'Grodzka',
                                          'Księdza Franciszka Maja']

    def test_by_size(self, client):
        body = search(client, '/api/places/', within='polska', order='size')
        assert names(body['results'])[:2] == ['Kraków', 'Łódź']

    def test_the_default_without_a_query_is_size(self, client):
        body = search(client, '/api/streets/', within='krakow')
        assert names(body['results']) == ['Aleja 3 Maja', 'Grodzka', 'Floriańska',
                                          'Księdza Franciszka Maja']

    def test_by_number_across_types(self, client):
        """Streets have no number and sort last, rather than failing the request."""
        body = search(client, '/api/search/', within='stare-miasto', type='streets,addresses',
                      order='number')
        assert [h.get('house_number') for h in body['results']] == [
            '1', '3', '5', '12', '12a', '13', '14', '14a', '15', '52', '52A', None, None]
        assert {h['type'] for h in body['results'][-2:]} == {'streets'}


class TestLanguage:
    def test_names_in_english(self, client):
        hit = first(client, 'Floriańska 14, Kraków', lang='en')
        assert hit['path'] == ['Poland', 'Lesser Poland Voivodeship', 'Cracow', 'Stare Miasto',
                               'Floriańska', 'Floriańska 14']

    def test_what_has_no_translation_keeps_its_name(self, client):
        assert first(client, 'Gajcego 14', lang='en')['name'] == 'Tadeusza Gajcego 14'

    def test_a_language_nothing_has_changes_nothing(self, client):
        assert first(client, 'Kraków', type='places', lang='xx')['path'] == [
            'Polska', 'województwo małopolskie', 'Kraków']

    def test_the_slug_stays_as_it_is(self, client):
        hit = first(client, 'Kraków', type='places', lang='de')
        assert (hit['name'], hit['slug']) == ('Krakau', 'krakow')


class TestTheShapeOfAHit:
    def test_no_database_id(self, client):
        """Each country is built in a database of its own, so two hand out the same ids."""
        for hit in search(client, q='Floriańska')['results']:
            assert 'id' not in hit

    def test_a_score_with_a_query(self, client):
        assert first(client, 'Kraków')['score'] > 0

    def test_no_geometry_unless_asked(self, client):
        hit = first(client, 'Kraków', type='places')
        assert 'geometry' not in hit
        assert hit['center'] and hit['bbox']

    @pytest.mark.parametrize('kind,q,shape', [
        ('places', 'Kraków', 'MultiPolygon'),
        ('country-subareas', 'małopolskie', 'MultiPolygon'),
        ('place-subareas', 'Kazimierz', 'MultiPolygon'),
        ('streets', 'Floriańska', 'MultiLineString'),
    ])
    def test_geometry_when_asked(self, client, kind, q, shape):
        assert first(client, q, type=kind, geometry='true')['geometry']['type'] == shape

    def test_an_address_has_a_point_and_no_geometry(self, client):
        hit = first(client, 'Floriańska 14 Kraków', geometry='true')
        assert hit['geometry'] is None
        assert distance_m(hit['center'], FLORIANSKA_14) < 1

    def test_the_total_counts_every_match(self, client):
        body = search(client, q='Gajcego', type='addresses', page_size=2)
        assert (body['total'], len(body['results'])) == (7, 2)


class TestDetail:
    def get(self, client, path, **params):
        return client.get(path, params)

    def test_a_city(self, client):
        body = self.get(client, '/api/places/krakow/').json()
        assert (body['name'], body['population']) == ('Kraków', 804237)
        assert body['geometry']['type'] == 'MultiPolygon'
        assert body['path'] == ['Polska', 'województwo małopolskie', 'Kraków']

    def test_a_street(self, client):
        body = self.get(client, '/api/streets/florianska-lodz/').json()
        assert body['geometry']['type'] == 'MultiLineString'
        assert 'lodz' in body['ancestry_slugs']

    def test_an_address(self, client):
        body = self.get(client, '/api/addresses/florianska-krakow-12a/').json()
        assert (body['name'], body['house_number_sort']) == ('Floriańska 12a', 12)

    def test_in_another_language(self, client):
        body = self.get(client, '/api/places/krakow/', lang='en').json()
        assert body['name'] == 'Cracow'
        assert body['path'] == ['Poland', 'Lesser Poland Voivodeship', 'Cracow']

    def test_a_slug_two_countries_share_is_a_409(self, client):
        response = self.get(client, '/api/places/lipno/')
        assert response.status_code == 409
        assert sorted(c['ancestry_slugs'][0] for c in response.json()['candidates']) == [
            'cesko', 'polska']

    def test_within_says_which(self, client):
        response = self.get(client, '/api/places/lipno/', within='polska')
        assert response.status_code == 200
        assert response.json()['population'] == 14000

    def test_an_unknown_slug_is_a_404(self, client):
        assert self.get(client, '/api/places/nibylandia/').status_code == 404

    def test_an_unknown_type_is_a_404(self, client):
        assert self.get(client, '/api/villages/krakow/').status_code == 404

    def test_a_slug_of_another_type_is_a_404(self, client):
        """krakow is a place and a county; it is not a street."""
        assert self.get(client, '/api/streets/krakow/').status_code == 404


class TestMistakes:
    @pytest.mark.parametrize('params', [
        {'type': 'wioski'},
        {'exclude_type': 'wioski'},
        {'type': 'streets', 'exclude_type': 'streets'},
        {'bbox': '19.3,51.6,19.7'},
        {'bbox': 'a,b,c,d'},
        {'near': '19.94'},
        {'page': '0'},
        {'page_size': 'dużo'},
        {'admin_level': 'cztery'},
    ])
    def test_are_a_400_that_says_what(self, client, params):
        response = client.get('/api/search/', params)
        assert response.status_code == 400
        assert response.json()['detail']

    def test_a_page_too_big_is_cut_down(self, client):
        assert search(client, page_size='10000')['page_size'] == 200

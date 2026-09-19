import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon


def square(x, y, size=1.0):
    return Polygon((
        (x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y),
    ))


@pytest.fixture
def country(db):
    from django_osm_locations.models import Country
    # migration 0031 seeds this row, so take it rather than making a second one
    # that would violate the (iso2, iso3, un) constraint
    country, _ = Country.objects.get_or_create(
        iso2='PL', iso3='POL', un=616,
        defaults={'name': 'Polska', 'slug': 'polska'},
    )
    country.border = MultiPolygon(square(0, 0, 10))
    country.save()
    return country


@pytest.fixture
def place(db, country):
    from django_osm_locations.models import Place
    return Place.objects.create(name='Kraków', border=MultiPolygon(square(1, 1)))


@pytest.fixture
def place_with_umlaut(db):
    from django_osm_locations.models import Place
    return Place.objects.create(name='München', border=MultiPolygon(square(3, 3)))


@pytest.fixture
def street(db):
    from django_osm_locations.models import Street
    from django.contrib.gis.geos import LineString, MultiLineString
    return Street.objects.create(
        name='Floriańska',
        lines=MultiLineString(LineString((1.1, 1.1), (1.2, 1.2))),
    )


def results(response):
    """
    DRF returns a bare list unless the project configures pagination, and this
    package does not impose one. Accept either shape.
    """
    payload = response.json()
    return payload['results'] if isinstance(payload, dict) else payload

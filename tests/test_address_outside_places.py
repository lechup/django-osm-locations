"""
An address that no place contains still belongs to a country.

An address took its country from the place containing it, and a village is a
place node with no border: 1,635,466 of Poland's 8.5 million addresses and
418,762 of Austria's were contained by nothing, had no country, and the
per-country index -- which picks rows by country -- left every one of them out
of the published snapshots.
"""
import pytest
from django.contrib.gis.geos import LineString, MultiLineString, MultiPolygon, Point, Polygon

from django_osm_locations.management.commands.locations_recalculate_denormalized_fields import (
    Command as Denorm,
)
from django_osm_locations.models import Address, Country, Place, Street

WEST = [['Westland', 'westland']]
EAST = [['Eastland', 'eastland']]


def box(x0, x1, y0=50.0, y1=51.0):
    return MultiPolygon(Polygon(((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))))


def pairs(array):
    return [list(pair) for pair in array or []]


@pytest.fixture
def world(db):
    Country.objects.create(name='Westland', slug='westland', iso2='W1', iso3='WS1',
                           un=931, border=box(19.0, 20.0))
    Country.objects.create(name='Eastland', slug='eastland', iso2='E1', iso3='ES1',
                           un=932, border=box(20.0, 21.0))
    # a village: a node, no border, so it contains nothing
    village = Place.objects.create(
        name='Wioska', slug='wioska', center=Point(19.90, 50.5),
        country=WEST, countrysubarea=[['Region', 'region']])
    # nearer to the border on the other side, so a search ignoring the
    # country would take it
    Place.objects.create(
        name='Dorf', slug='dorf', center=Point(20.02, 50.5),
        country=EAST, countrysubarea=[['Bezirk', 'bezirk']])
    road = Street.objects.create(
        name='Polna', slug='polna', center=Point(19.95, 50.5),
        lines=MultiLineString(LineString((19.949, 50.5), (19.951, 50.5))),
        country=WEST, countrysubarea=[['Region', 'region']],
        place=[['Wioska', 'wioska']])
    return village, road


def address_at(x, y, street_name='', **kwargs):
    return Address.objects.create(
        name='{} 1'.format(street_name or 'Farm'), house_number='1',
        street_name=street_name, center=Point(x, y),
        country=kwargs.get('country', []), countrysubarea=kwargs.get('countrysubarea', []),
        place=kwargs.get('place', []))


@pytest.mark.django_db(transaction=True)
class TestFromTheStreetItNames:
    def test_it_takes_the_streets_country_region_and_place(self, world):
        a = address_at(19.95, 50.5001, street_name='Polna')
        Denorm.update_address_street_field()
        a.refresh_from_db()
        assert pairs(a.street) == [['Polna', 'polna']]
        assert pairs(a.country) == WEST
        assert pairs(a.countrysubarea) == [['Region', 'region']]
        assert pairs(a.place) == [['Wioska', 'wioska']]

    def test_an_address_a_place_already_placed_keeps_it(self, world):
        a = address_at(19.95, 50.5001, street_name='Polna', country=[['Town', 'town']],
                       place=[['Town', 'town']])
        Denorm.update_address_street_field()
        a.refresh_from_db()
        assert pairs(a.street) == [['Polna', 'polna']]
        assert pairs(a.country) == [['Town', 'town']]
        assert pairs(a.place) == [['Town', 'town']]


@pytest.mark.django_db(transaction=True)
class TestByBorderAndNearestPlace:
    def test_it_takes_the_country_its_border_contains_it_in(self, world):
        farm = address_at(19.97, 50.5)
        Denorm.place_addresses_outside_places()
        farm.refresh_from_db()
        assert pairs(farm.country) == WEST

    def test_the_nearest_place_in_that_country_not_across_the_border(self, world):
        """Dorf is nearer; it is in the other country."""
        farm = address_at(19.99, 50.5)
        Denorm.place_addresses_outside_places()
        farm.refresh_from_db()
        assert pairs(farm.place) == [['Wioska', 'wioska']]
        assert pairs(farm.countrysubarea) == [['Region', 'region']]

    def test_beyond_any_place_it_still_gets_its_country(self, world):
        Place.objects.all().delete()
        farm = address_at(19.5, 50.9)
        Denorm.place_addresses_outside_places()
        farm.refresh_from_db()
        assert pairs(farm.country) == WEST

    def test_an_address_already_placed_is_left_alone(self, world):
        a = address_at(19.97, 50.5, country=EAST)
        Denorm.place_addresses_outside_places()
        a.refresh_from_db()
        assert pairs(a.country) == EAST

    def test_outside_every_border_it_stays_unplaced(self, world):
        at_sea = address_at(19.5, 48.0)
        Denorm.place_addresses_outside_places()
        at_sea.refresh_from_db()
        assert pairs(at_sea.country) == []


@pytest.mark.django_db(transaction=True)
class TestCleaningLeavesTheCountry:
    """Emptying it cost every address an update of all its indexes, twice."""

    def test_an_address_keeps_its_country_and_loses_its_place(self, world):
        a = address_at(19.95, 50.5, country=WEST, place=[['Wioska', 'wioska']])
        Denorm.clean_denorm_fields_before_update()
        a.refresh_from_db()
        assert pairs(a.country) == WEST
        assert pairs(a.place) == []

    def test_a_street_is_still_emptied(self, world):
        _, road = world
        Denorm.clean_denorm_fields_before_update()
        road.refresh_from_db()
        assert pairs(road.country) == []

    def test_a_row_with_nothing_to_empty_is_not_rewritten(self, world):
        """A country fresh from its import: rewriting it changed nothing, slowly."""
        from django.db import connection

        empty = address_at(19.95, 50.5, country=WEST)
        filled = address_at(19.96, 50.5, country=WEST, place=[['Wioska', 'wioska']])

        def xmin(pk):
            with connection.cursor() as cursor:
                cursor.execute('SELECT xmin::text FROM locations_address WHERE id = %s', [pk])
                return cursor.fetchone()[0]

        before = xmin(empty.pk), xmin(filled.pk)
        Denorm.clean_denorm_fields_before_update()
        assert xmin(empty.pk) == before[0]
        assert xmin(filled.pk) != before[1]

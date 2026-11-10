"""
Recalculating one country, and leaving the others exactly as they were.

The walk was global: adding or re-importing one country recalculated every
country in the database, ten hours for Poland and Denmark and more with each
addition, although slugs are unique per country and nothing in one changes
when another does.
"""
from unittest import mock

import pytest
from django.contrib.gis.geos import LineString, MultiLineString, MultiPolygon, Point, Polygon
from django.core.management.base import CommandError
from django.db import connection

from django_osm_locations.management.commands.locations_recalculate_all import (
    Command as RecalculateAll,
)
from django_osm_locations.management.commands.locations_recalculate_denormalized_fields import (
    Command as Denorm,
)
from django_osm_locations.models import Address, Country, CountrySubarea, Place, Street
from django_osm_locations.utils.recalc_scope import RecalcScope


def box(x0, x1, y0=50.0, y1=51.0):
    return MultiPolygon(Polygon(((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))))


def street(name, slug, x, y=50.5, **kwargs):
    kwargs.setdefault('country', [])
    kwargs.setdefault('place', [])
    return Street.objects.create(
        name=name, slug=slug,
        lines=MultiLineString(LineString((x - 0.001, y), (x + 0.001, y))), **kwargs)


@pytest.fixture
def two_countries(db):
    # Westland is x in [19, 20), Eastland x in [20, 21)
    west = Country.objects.create(name='Westland', slug='westland', iso2='W1', iso3='WS1',
                                  un=931, border=box(19.0, 20.0))
    east = Country.objects.create(name='Eastland', slug='eastland', iso2='E1', iso3='ES1',
                                  un=932, border=box(20.0, 21.0))
    return west, east


@pytest.fixture
def scope_off():
    yield
    RecalcScope.country = None
    with connection.cursor() as cursor:
        cursor.execute('DROP TABLE IF EXISTS "locations_recalc_scope"')


class TestWhatBelongsToACountry:
    def members(self, west):
        RecalcScope.build(west, [Street])
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM locations_recalc_scope WHERE model = 'street'")
            return {row[0] for row in cursor.fetchall()}

    def test_a_row_already_labelled_with_it(self, two_countries, scope_off):
        west, _ = two_countries
        labelled = street('A', 'a', 19.5, country=[['Westland', 'westland']])
        assert labelled.id in self.members(west)

    def test_a_row_labelled_with_another_is_not_even_inside_its_border(self, two_countries, scope_off):
        """The label wins: a previous run already decided where it is."""
        west, _ = two_countries
        elsewhere = street('B', 'b', 19.5, country=[['Eastland', 'eastland']])
        assert elsewhere.id not in self.members(west)

    def test_a_fresh_row_inside_its_border(self, two_countries, scope_off):
        west, _ = two_countries
        fresh = street('C', 'c', 19.5)
        assert fresh.id in self.members(west)

    def test_a_fresh_row_inside_the_neighbour_is_not(self, two_countries, scope_off):
        west, _ = two_countries
        across = street('D', 'd', 20.002)
        assert across.id not in self.members(west)

    def test_a_fresh_row_just_off_the_coast_goes_to_the_nearest_country(self, two_countries, scope_off):
        """A pier's centre after the border was clipped to the land."""
        west, _ = two_countries
        pier = street('E', 'e', 19.5, y=49.99)
        assert pier.id in self.members(west)

    def test_a_fresh_row_far_from_everything_is_nobodys(self, two_countries, scope_off):
        west, _ = two_countries
        lost = street('F', 'f', 19.5, y=48.0)
        assert lost.id not in self.members(west)

    def test_a_fresh_row_off_the_neighbours_coast_is_the_neighbours(self, two_countries, scope_off):
        """Within reach of both, nearer the east: the nearest border decides."""
        west, _ = two_countries
        pier = street('G', 'g', 20.01, y=49.99)
        assert pier.id not in self.members(west)

    def test_the_border_tiles_are_gone_afterwards(self, two_countries, scope_off):
        west, _ = two_countries
        self.members(west)
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('locations_recalc_border_tiles')")
            assert cursor.fetchone()[0] is None


class TestEveryStepIsRestricted:
    """Each builder takes the scope clause when a scope is set, and none when not."""

    BUILDERS = {
        'push-down': lambda: Denorm.get_sql_for_update_denorm_fields(
            'street', 'country', 'locations_country'),
        'address link': Denorm.get_sql_for_address_street_link,
        'settlement vote': Denorm.get_sql_for_street_settlement_from_addresses,
        'placeless country': Denorm.get_sql_for_placeless_street_country,
        'fallback place': Denorm.get_sql_for_street_place_field,
        'street inherit': Denorm.get_sql_for_street_inherited_ancestry,
        'address map': Denorm.get_sql_for_address_place_map,
        'address slugs': Denorm.get_sql_for_address_slugs,
        'addresses outside places': Denorm.get_sql_for_address_outside_places,
        'namesake streets': lambda: Denorm.get_sql_for_namesake_streets()[0],
        'address link in its place': Denorm.get_sql_for_address_street_in_place,
        'repeated houses': Denorm.get_sql_for_repeated_houses,
        'shared address slugs': Denorm.get_sql_for_shared_address_slugs,
    }

    @pytest.mark.parametrize('name', sorted(BUILDERS))
    def test_scoped(self, name):
        RecalcScope.country = mock.Mock(pk=1, id=1)
        try:
            sql = self.BUILDERS[name]()
        finally:
            RecalcScope.country = None
        assert 'locations_recalc_scope' in sql
        assert '{' not in sql and '}' not in sql

    @pytest.mark.parametrize('name', sorted(BUILDERS))
    def test_unscoped(self, name):
        sql = self.BUILDERS[name]()
        assert 'locations_recalc_scope' not in sql
        assert '{' not in sql and '}' not in sql

    @pytest.mark.parametrize('name', sorted(BUILDERS))
    def test_the_scope_never_adds_a_placeholder(self, name):
        """These are executed with bound parameters; a %s would shift them."""
        unscoped = self.BUILDERS[name]().count('%')
        RecalcScope.country = mock.Mock(pk=1, id=1)
        try:
            scoped = self.BUILDERS[name]().count('%')
        finally:
            RecalcScope.country = None
        assert scoped == unscoped


@pytest.mark.django_db(transaction=True)
class TestTheOtherCountryIsLeftAlone:
    @pytest.fixture
    def world(self, two_countries):
        west, east = two_countries
        rows = {}
        for country, x in ((west, 19.5), (east, 20.5)):
            key = country.slug
            CountrySubarea.objects.create(
                name='Region ' + key, slug='region-' + key, border=box(x - 0.4, x + 0.4, 50.1, 50.9))
            Place.objects.create(
                name='Town ' + key, slug='town-' + key, border=box(x - 0.1, x + 0.1, 50.4, 50.6))
            rows[key] = {
                'street': street('Main', 'main', x),
                'address': Address.objects.create(
                    name='Main 1', house_number='1', street_name='Main', center=Point(x, 50.5001)),
            }
        # the east as a previous run left it, with values the west's run must not touch
        Street.objects.filter(pk=rows['eastland']['street'].pk).update(
            country=[['Eastland', 'eastland']], place=[['Town eastland', 'town-eastland']],
            slug='main-sentinel')
        Address.objects.filter(pk=rows['eastland']['address'].pk).update(
            country=[['Eastland', 'eastland']], slug='sentinel-1')
        return west, east, rows

    def run(self, country):
        with mock.patch.object(RecalculateAll, '_resolve_country', return_value=country):
            RecalculateAll().handle(country='whatever')

    def test_the_scoped_country_is_recalculated(self, world, scope_off):
        west, _, rows = world
        self.run(west)
        s = Street.objects.get(pk=rows['westland']['street'].pk)
        a = Address.objects.get(pk=rows['westland']['address'].pk)
        assert [list(p) for p in s.country] == [['Westland', 'westland']]
        assert [list(p) for p in a.country] == [['Westland', 'westland']]
        assert [list(p) for p in a.place] == [['Town westland', 'town-westland']]

    def test_the_other_country_is_not_touched(self, world, scope_off):
        west, _, rows = world
        self.run(west)
        s = Street.objects.get(pk=rows['eastland']['street'].pk)
        a = Address.objects.get(pk=rows['eastland']['address'].pk)
        assert s.slug == 'main-sentinel'
        assert [list(p) for p in s.place] == [['Town eastland', 'town-eastland']]
        assert a.slug == 'sentinel-1'
        assert [list(p) for p in a.country] == [['Eastland', 'eastland']]

    def test_the_scope_is_gone_afterwards(self, world, scope_off):
        west, _, _ = world
        self.run(west)
        assert not RecalcScope.active()
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('locations_recalc_scope')")
            assert cursor.fetchone()[0] is None


class TestRefusals:
    def test_an_unknown_country_is_named(self):
        with pytest.raises(CommandError, match='Unknown country'):
            RecalculateAll._resolve_country('atlantis')

    @pytest.mark.django_db
    def test_one_pass_is_not_combined_with_a_scope(self, two_countries):
        west, _ = two_countries
        with mock.patch.object(RecalculateAll, '_resolve_country', return_value=west):
            with pytest.raises(CommandError, match='--one-pass'):
                RecalculateAll().handle(country='whatever', one_pass=True)


@pytest.mark.django_db(transaction=True)
class TestAPlaceSlugSharedAcrossCountries:
    """
    A slug is unique within a country, not across countries: 84 place slugs are
    shared between Poland, Denmark and Britain. The street inheritance joined on
    the slug alone and took whichever place came first -- a run left 674 streets
    outside Denmark labelled Danish. The nearest place bearing the slug is taken.
    """

    def test_the_street_takes_the_country_of_the_nearby_namesake(self, two_countries):
        # the near namesake first and the far one second: an arbitrary-match
        # join on this data takes the later row, which is the far one
        Place.objects.create(name='Centrum', slug='centrum', center=Point(19.5, 50.5),
                             country=[['Westland', 'westland']])
        Place.objects.create(name='Centrum', slug='centrum', center=Point(20.9, 50.5),
                             country=[['Eastland', 'eastland']])
        s = street('Rynek', 'rynek', 19.5, place=[['Centrum', 'centrum']])
        s.refresh_from_db()

        Denorm.inherit_street_ancestry_from_place()

        s.refresh_from_db()
        assert [list(p) for p in s.country] == [['Westland', 'westland']]

    #: The join as it was, for proving the fixture reproduces the bug.
    SLUG_ONLY = """
        UPDATE "locations_street" AS s SET
            country = p.country, countrysubarea = p.countrysubarea
        FROM "locations_place" p
        WHERE p.slug = coalesce(nullif(s.place[1][2], ''), s.place[2][2])
          AND coalesce(nullif(s.place[1][2], ''), s.place[2][2]) IS NOT NULL;
    """

    def test_the_slug_alone_would_have_taken_the_far_one(self, two_countries):
        """Without this the test above could pass by luck and prove nothing."""
        # the near namesake first and the far one second: an arbitrary-match
        # join on this data takes the later row, which is the far one
        Place.objects.create(name='Centrum', slug='centrum', center=Point(19.5, 50.5),
                             country=[['Westland', 'westland']])
        Place.objects.create(name='Centrum', slug='centrum', center=Point(20.9, 50.5),
                             country=[['Eastland', 'eastland']])
        s = street('Rynek', 'rynek', 19.5, place=[['Centrum', 'centrum']])

        with connection.cursor() as cursor:
            cursor.execute(self.SLUG_ONLY)

        s.refresh_from_db()
        assert [list(p) for p in s.country] == [['Eastland', 'eastland']]

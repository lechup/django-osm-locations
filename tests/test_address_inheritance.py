"""
An address inherits its country and country subarea from its place.

It used to work them out for itself, by point-in-polygon, which meant four
passes over 11 million rows. Measured on this data: pushing Place down one
ancestor at a time is 27,993 statements at 1.88s each, or 14h36m, and Country
and CountrySubarea each cost another pass against much larger borders.
Aggregating the statement did not help -- a single statement for the level and
batches of 2,000 both ran past generous timeouts -- because the cost is the
containment test, not the statement issuing it. An address inside a place is
inside that place's country by construction, so three of the four passes were
computing what a join already knew.
"""
import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon

from django_osm_locations.management.commands.locations_recalculate_denormalized_fields import (
    Command as Denorm,
)
from django_osm_locations.models import Address, Country, CountrySubarea, Place


def square(x, y, size=1.0):
    return MultiPolygon(Polygon((
        (x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y),
    )))


@pytest.fixture
def hierarchy(db):
    """A country holding one subarea holding two nested places."""
    country = Country.objects.create(
        name='Testland', slug='testland', iso2='T1', iso3='TS1', un=911,
        border=square(20, 50, 4))
    subarea = CountrySubarea.objects.create(
        name='Region', slug='region', border=square(20, 50, 3),
        country=[['Testland', 'testland']],
        countrysubarea=[['Region', 'region']])
    big = Place.objects.create(
        name='Big', slug='big', border=square(20, 50, 2), area=4.0,
        country=[['Testland', 'testland']],
        countrysubarea=[['Region', 'region']])
    small = Place.objects.create(
        name='Small', slug='small', border=square(20, 50, 1), area=1.0,
        country=[['Testland', 'testland']],
        countrysubarea=[['Region', 'region']])
    return country, subarea, big, small


def address_at(x, y, **kwargs):
    kwargs.setdefault('name', 'Somewhere 1')
    kwargs.setdefault('house_number', '1')
    kwargs.setdefault('country', [['', '']])
    kwargs.setdefault('countrysubarea', [['', '']])
    kwargs.setdefault('place', [['', '']])
    return Address.objects.create(center=Point(x, y), **kwargs)


@pytest.mark.django_db(transaction=True)
class TestWhatAnAddressInherits:
    def test_it_takes_the_country_from_its_place(self, hierarchy):
        address = address_at(20.5, 50.5)
        Denorm.inherit_address_ancestry_from_place()
        address.refresh_from_db()
        assert ['Testland', 'testland'] in [list(pair) for pair in address.country]

    def test_it_takes_the_country_subarea_too(self, hierarchy):
        address = address_at(20.5, 50.5)
        Denorm.inherit_address_ancestry_from_place()
        address.refresh_from_db()
        assert ['Region', 'region'] in [list(pair) for pair in address.countrysubarea]

    def test_the_smallest_containing_place_wins(self, hierarchy):
        """
        Both places contain the point. Two places over one point is a boundary
        anomaly, and the more specific one is the better answer -- where the
        containment pass appended both.
        """
        address = address_at(20.5, 50.5)
        Denorm.inherit_address_ancestry_from_place()
        address.refresh_from_db()
        named = [list(pair) for pair in address.place]
        assert ['Small', 'small'] in named
        assert ['Big', 'big'] not in named

    def test_a_place_further_out_is_still_reached(self, hierarchy):
        """Inside Big but outside Small, so Big is the answer."""
        address = address_at(21.5, 51.5)
        Denorm.inherit_address_ancestry_from_place()
        address.refresh_from_db()
        named = [list(pair) for pair in address.place]
        assert ['Big', 'big'] in named
        assert ['Small', 'small'] not in named

    def test_the_first_element_is_the_real_ancestor(self, hierarchy):
        """
        No placeholder any more: element [1] is the country itself, which is
        what the slug and scope expressions read.
        """
        address = address_at(20.5, 50.5, country=[], countrysubarea=[], place=[])
        Denorm.inherit_address_ancestry_from_place()
        address.refresh_from_db()
        assert list(address.country[0]) == ['Testland', 'testland']
        assert list(address.place[0]) == ['Small', 'small']


@pytest.mark.django_db(transaction=True)
class TestAnAddressWithNoPlace:
    def test_it_is_left_alone_rather_than_failing(self, hierarchy):
        """
        Outside every border. 713 addresses in the real data have no country at
        all, so this state already exists and must not raise.
        """
        address = address_at(40.0, 10.0)
        Denorm.inherit_address_ancestry_from_place()
        address.refresh_from_db()
        assert [list(p) for p in address.country] == [['', '']]

    def test_it_does_not_stop_the_others(self, hierarchy):
        orphan = address_at(40.0, 10.0)
        inside = address_at(20.5, 50.5)
        Denorm.inherit_address_ancestry_from_place()
        orphan.refresh_from_db()
        inside.refresh_from_db()
        assert [list(p) for p in orphan.country] == [['', '']]
        assert ['Testland', 'testland'] in [list(p) for p in inside.country]


class TestTheContainmentPassesAreGone:
    @pytest.mark.parametrize('ancestor', ['Country', 'CountrySubarea', 'Place'])
    def test_address_is_no_longer_pushed_down_by_containment(self, ancestor):
        assert Address not in Denorm.DENORMALIZED_MODEL_CLASSES_TO_UPDATE_DICT[ancestor]

    def test_but_its_place_subarea_still_is(self):
        """Nothing above a place subarea for an address to inherit from."""
        assert Address in Denorm.DENORMALIZED_MODEL_CLASSES_TO_UPDATE_DICT['PlaceSubarea']

    def test_the_walk_runs_it(self):
        import inspect

        from django_osm_locations.management.commands.locations_recalculate_all import (
            Command as RecalculateAll,
        )

        source = inspect.getsource(RecalculateAll.handle)
        assert 'inherit_address_ancestry_from_place' in source
        assert source.index('inherit_address_ancestry_from_place') < \
            source.index('update_address_slugs')


@pytest.mark.django_db(transaction=True)
class TestAStreetInheritsToo:
    """
    Measured on this data, a street was rewritten 7.01 times per row against an
    address's 1.00, because it still went through all four levels by containment
    while an address went through one.
    """

    @staticmethod
    def street_in(place_pair):
        from django.contrib.gis.geos import LineString, MultiLineString

        from django_osm_locations.models import Street

        return Street.objects.create(
            name='Testowa', slug='testowa',
            lines=MultiLineString(LineString((20.4, 50.4), (20.6, 50.6))),
            country=[['', '']], countrysubarea=[['', '']], place=place_pair)

    def test_it_takes_the_country_from_its_place(self, hierarchy):
        street = self.street_in([['', ''], ['Small', 'small']])
        Denorm.inherit_street_ancestry_from_place()
        street.refresh_from_db()
        assert ['Testland', 'testland'] in [list(p) for p in street.country]
        assert ['Region', 'region'] in [list(p) for p in street.countrysubarea]

    def test_it_works_when_the_place_was_overwritten_not_appended(self, hierarchy):
        """
        update_street_place_field replaces the array outright, so the slug sits
        at element 1 there and at element 2 after the containment pass. Both
        have to be read.
        """
        street = self.street_in([['Small', 'small']])
        Denorm.inherit_street_ancestry_from_place()
        street.refresh_from_db()
        assert ['Testland', 'testland'] in [list(p) for p in street.country]

    def test_a_street_with_no_place_is_left_alone(self, hierarchy):
        street = self.street_in([['', '']])
        Denorm.inherit_street_ancestry_from_place()
        street.refresh_from_db()
        assert [list(p) for p in street.country] == [['', '']]


class TestStreetsLeaveTheTopLevels:
    @pytest.mark.parametrize('ancestor', ['Country', 'CountrySubarea'])
    def test_no_longer_pushed_down_by_containment(self, ancestor):
        from django_osm_locations.models import Street

        assert Street not in Denorm.DENORMALIZED_MODEL_CLASSES_TO_UPDATE_DICT[ancestor]

    @pytest.mark.parametrize('ancestor', ['Place', 'PlaceSubarea'])
    def test_but_its_place_and_subarea_still_are(self, ancestor):
        """That is how a street gets a place at all."""
        from django_osm_locations.models import Street

        assert Street in Denorm.DENORMALIZED_MODEL_CLASSES_TO_UPDATE_DICT[ancestor]

    def test_the_walk_inherits_right_after_the_place_is_settled(self):
        import inspect

        from django_osm_locations.management.commands.locations_recalculate_all import (
            Command as RecalculateAll,
        )

        source = inspect.getsource(RecalculateAll.handle)
        assert source.index('update_street_place_field') < \
            source.index('inherit_street_ancestry_from_place')
        assert source.index('inherit_street_ancestry_from_place') < \
            source.index("'slugs: {}'")

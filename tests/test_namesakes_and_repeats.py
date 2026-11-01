"""
One street of each name in each place, and one address for each house.

The cases are the Poland pack's: Podkarpacka in pieces through a village
drawn as a point, Górczewska 124 in Warszawa 160 m from the line of its
street, Pawia 23 a building and an office inside it, Okólna 76 in Łódź and
in Łagiewniki Nowe.
"""
import itertools

import pytest
from django.contrib.gis.geos import LineString, MultiLineString, Point

from django_osm_locations.management.commands.locations_recalculate_denormalized_fields import (
    Command as Denorm,
)
from django_osm_locations.models import Address, Street

pytestmark = pytest.mark.django_db

WOLA = ['Wola', 'wola']
KOWALE = ['Kowale', 'kowale']
# a metre east and north, near 50 degrees north
EAST = 1 / (111320 * 0.6428)
NORTH = 1 / 110540


def street(name, place, x, y, metres=100, slug=None):
    lines = MultiLineString(LineString((x, y), (x + metres * EAST, y)))
    return Street.objects.create(name=name, slug=slug or '', lines=lines, place=[place],
                                 length=metres, center=Point(x, y))


OSM_IDS = itertools.count(1)


def address(number, x, y, street_name='Polna', place=WOLA, on=None, way=False, postcode=''):
    """A node, or with ``way`` the outline of a building."""
    linked = {'street': [[on.name, on.slug]]} if on else {}
    return Address.objects.create(
        name='{} {}'.format(street_name or place[0], number), house_number=number,
        street_name=street_name, center=Point(x, y), place=[place], postcode=postcode,
        **linked, **{'way_osm_id' if way else 'node_osm_id': next(OSM_IDS)})


def live(name):
    return list(Street.objects.filter(name=name, is_merged=False))


class TestOneStreetOfEachName:
    def test_the_pieces_in_a_village_are_one(self):
        """Wola has no border, so the import's own merge never saw them."""
        street('Polna', WOLA, 20.0, 50.0)
        street('Polna', WOLA, 20.0 + 400 * EAST, 50.0)
        Denorm.merge_namesake_streets()
        (polna,) = live('Polna')
        assert polna.lines.num_geom == 2
        assert polna.length == pytest.approx(200, rel=0.02)

    def test_touching_pieces_become_one_line(self):
        street('Polna', WOLA, 20.0, 50.0)
        street('Polna', WOLA, 20.0 + 100 * EAST, 50.0)
        Denorm.merge_namesake_streets()
        (polna,) = live('Polna')
        assert polna.lines.num_geom == 1

    def test_measured_again(self):
        street('Polna', WOLA, 20.0, 50.0)
        street('Polna', WOLA, 20.0, 50.0 + 1000 * NORTH)
        Denorm.merge_namesake_streets()
        (polna,) = live('Polna')
        assert polna.bbox == pytest.approx(list(polna.lines.extent))
        assert polna.lines.intersects(polna.center.buffer(1e-9))

    def test_the_other_pieces_are_marked_not_deleted(self):
        first = street('Polna', WOLA, 20.0, 50.0)
        second = street('Polna', WOLA, 20.0 + 400 * EAST, 50.0)
        Denorm.merge_namesake_streets()
        assert Street.objects.get(pk=first.pk).is_merged is False
        assert Street.objects.get(pk=second.pk).is_merged is True

    def test_another_place_keeps_its_own(self):
        street('Polna', WOLA, 20.0, 50.0)
        street('Polna', KOWALE, 20.5, 50.0)
        Denorm.merge_namesake_streets()
        assert len(live('Polna')) == 2

    def test_another_name_stays(self):
        street('Polna', WOLA, 20.0, 50.0)
        street('Leśna', WOLA, 20.0 + 400 * EAST, 50.0)
        Denorm.merge_namesake_streets()
        assert len(live('Polna')) == len(live('Leśna')) == 1

    def test_a_street_without_a_place_is_left_alone(self):
        for x in (20.0, 20.1):
            Street.objects.create(name='Polna', lines=MultiLineString(LineString((x, 50), (x + 0.001, 50))))
        Denorm.merge_namesake_streets()
        assert len(live('Polna')) == 2

    def test_twice_changes_nothing_more(self):
        street('Polna', WOLA, 20.0, 50.0)
        street('Polna', WOLA, 20.0 + 400 * EAST, 50.0)
        Denorm.merge_namesake_streets()
        Denorm.merge_namesake_streets()
        assert len(live('Polna')) == 1


class TestTheStreetInItsPlace:
    def test_however_far_from_the_line(self):
        """Górczewska 124 stands 160 m from its street, beyond the first link's 100 m."""
        polna = street('Polna', WOLA, 20.0, 50.0, slug='polna-wola')
        house = address('124', 20.0, 50.0 + 160 * NORTH)
        Denorm.link_addresses_to_streets_in_their_place()
        house.refresh_from_db()
        assert house.street == [['Polna', polna.slug]]

    def test_not_the_one_of_that_name_elsewhere(self):
        street('Polna', KOWALE, 20.0, 50.0, slug='polna-kowale')
        house = address('5', 20.0, 50.0 + 20 * NORTH)
        Denorm.link_addresses_to_streets_in_their_place()
        house.refresh_from_db()
        assert not house.street

    def test_not_a_piece_already_merged(self):
        street('Polna', WOLA, 20.0, 50.0, slug='polna-wola')
        merged = street('Polna', WOLA, 20.0, 50.0 + 300 * NORTH, slug='polna-wola-2')
        Street.objects.filter(pk=merged.pk).update(is_merged=True)
        house = address('5', 20.0, 50.0 + 290 * NORTH)
        Denorm.link_addresses_to_streets_in_their_place()
        house.refresh_from_db()
        assert house.street == [['Polna', 'polna-wola']]

    def test_an_address_already_linked_keeps_its_street(self):
        street('Polna', WOLA, 20.0, 50.0, slug='polna-wola')
        other = street('Polna', WOLA, 21.0, 50.0, slug='polna-wola-elsewhere')
        house = address('5', 20.0, 50.0 + 300 * NORTH, on=other)
        Denorm.link_addresses_to_streets_in_their_place()
        house.refresh_from_db()
        assert house.street == [['Polna', 'polna-wola-elsewhere']]

    def test_a_village_address_with_no_street_named_stays(self):
        street('Polna', WOLA, 20.0, 50.0, slug='polna-wola')
        house = address('5', 20.0, 50.0, street_name='')
        Denorm.link_addresses_to_streets_in_their_place()
        house.refresh_from_db()
        assert not house.street

    def test_the_temporary_index_is_gone_afterwards(self):
        from django.db import connection

        Denorm.link_addresses_to_streets_in_their_place()
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('locations_street_name_place_tmp')")
            assert cursor.fetchone()[0] is None


class TestOneAddressForEachHouse:
    @pytest.fixture
    def pawia(self):
        return street('Pawia', WOLA, 20.0, 50.0, slug='pawia-wola')

    def live(self):
        return list(Address.objects.filter(is_merged=False).order_by('id'))

    def test_a_building_and_the_office_in_it(self, pawia):
        building = address('23', 20.0, 50.0, 'Pawia', on=pawia, way=True)
        address('23', 20.0 + 12 * EAST, 50.0, 'Pawia', on=pawia, postcode='31-154')
        Denorm.merge_repeated_houses()
        (house,) = self.live()
        assert house.pk == building.pk
        assert house.center.coords == pytest.approx((20.0, 50.0))
        assert house.postcode == '31-154'

    def test_two_buildings_between_them(self, pawia):
        address('38', 20.0, 50.0, 'Pawia', on=pawia, way=True)
        address('38', 20.0 + 40 * EAST, 50.0, 'Pawia', on=pawia, way=True)
        Denorm.merge_repeated_houses()
        (house,) = self.live()
        assert house.center.coords == pytest.approx((20.0 + 20 * EAST, 50.0))
        assert house.bbox == pytest.approx(house.near_bbox())

    def test_far_apart_they_are_two_houses(self, pawia):
        """Okólna 76 in Łódź and in Łagiewniki Nowe."""
        address('76', 20.0, 50.0, 'Pawia', on=pawia)
        address('76', 20.0 + 6000 * EAST, 50.0, 'Pawia', on=pawia)
        Denorm.merge_repeated_houses()
        assert len(self.live()) == 2

    def test_another_number_is_another_house(self, pawia):
        address('23', 20.0, 50.0, 'Pawia', on=pawia)
        address('25', 20.0, 50.0, 'Pawia', on=pawia)
        Denorm.merge_repeated_houses()
        assert len(self.live()) == 2

    def test_12a_and_12A_are_one(self, pawia):
        address('12a', 20.0, 50.0, 'Pawia', on=pawia)
        address('12A', 20.0 + 5 * EAST, 50.0, 'Pawia', on=pawia)
        Denorm.merge_repeated_houses()
        assert len(self.live()) == 1

    def test_without_a_street_by_the_street_they_name(self):
        """Their slug is their place's, so wola-5 is house 5 on every street of Wola."""
        address('5', 20.0, 50.0, 'Polna')
        address('5', 20.0 + 10 * EAST, 50.0, 'Polna')
        address('5', 20.0 + 20 * EAST, 50.0, 'Leśna')
        Denorm.merge_repeated_houses()
        assert sorted(a.street_name for a in self.live()) == ['Leśna', 'Polna']

    def test_village_addresses_with_no_street_by_their_place(self):
        address('5', 20.0, 50.0, street_name='')
        address('5', 20.0 + 10 * EAST, 50.0, street_name='')
        address('5', 20.0, 50.0, street_name='', place=KOWALE)
        Denorm.merge_repeated_houses()
        assert len(self.live()) == 2

    def test_twice_changes_nothing_more(self, pawia):
        address('23', 20.0, 50.0, 'Pawia', on=pawia, way=True)
        address('23', 20.0 + 12 * EAST, 50.0, 'Pawia', on=pawia)
        Denorm.merge_repeated_houses()
        Denorm.merge_repeated_houses()
        assert len(self.live()) == 1

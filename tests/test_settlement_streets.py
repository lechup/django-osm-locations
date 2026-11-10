"""
A street of one name in each village of a municipality, and a slug of its
own for each house -- in the database, as the import and the recalculation
leave them.

The cases are the packs': Østergade in Rudkøbing and in Bagenkop, both in
Langeland, 45 km apart; kearns-5935, three houses on streets none was linked
to; Furth an der Triesting 46, twice, on no street.
"""
import itertools

import pytest
from django.contrib.gis.geos import LineString, MultiLineString, MultiPolygon, Point

from django_osm_locations.management.commands.importers.base import BaseImporter
from django_osm_locations.management.commands.locations_recalculate_denormalized_fields import (
    Command as Denorm,
)
from django_osm_locations.models import Address, Place, Street

from .conftest import square

pytestmark = pytest.mark.django_db

LANGELAND = ['Langeland', 'langeland']
# a metre east, near 55 degrees north
EAST = 1 / (111320 * 0.5736)
WAYS = itertools.count(1)


def street(x, metres=300, name='Østergade', place=LANGELAND):
    lines = MultiLineString(LineString((10 + x * EAST, 55.0), (10 + (x + metres) * EAST, 55.0)))
    return Street.objects.create(name=name, way_osm_id=next(WAYS), place=[place], lines=lines,
                                 length=metres, center=Point(10 + x * EAST, 55.0))


def house(x, settlement, number='12', street_name='Østergade', place=LANGELAND, on=None,
          slug=''):
    linked = {'street': [[on.name, on.slug]]} if on else {}
    return Address.objects.create(
        name='{} {}'.format(street_name or place[0], number), house_number=number,
        street_name=street_name, settlement_name=settlement, center=Point(10 + x * EAST, 55.0),
        place=[place], node_osm_id=next(WAYS), slug=slug, **linked)


def live(name='Østergade'):
    return sorted(Street.objects.filter(name=name, is_merged=False), key=lambda s: s.pk)


class TestTheRecalculation:
    def test_a_street_in_each_village(self):
        street(0), street(45000)
        house(100, 'Rudkøbing'), house(45100, 'Bagenkop')
        Denorm.merge_namesake_streets()
        assert len(live()) == 2

    def test_one_village_one_street(self):
        """A gap in the line is still the same street."""
        street(0), street(3000)
        house(100, 'Rudkøbing'), house(3100, 'Rudkøbing')
        Denorm.merge_namesake_streets()
        assert len(live()) == 1

    def test_a_piece_with_no_houses_goes_with_the_nearest(self):
        street(0), street(45000), street(45800, 100)
        house(100, 'Rudkøbing'), house(45100, 'Bagenkop')
        Denorm.merge_namesake_streets()
        rudkobing, bagenkop = live()
        assert (rudkobing.lines.num_geom, bagenkop.lines.num_geom) == (1, 2)

    def test_a_road_on_from_one_village_into_the_next_stays_one(self):
        street(0), street(310)
        house(100, 'Rudkøbing'), house(500, 'Tranekær')
        Denorm.merge_namesake_streets()
        assert len(live()) == 1


class TestTheImport:
    @pytest.fixture
    def langeland(self):
        return Place.objects.create(name='Langeland', slug='langeland',
                                    border=MultiPolygon(square(9.9, 54.9, 1.0)))

    def test_a_street_in_each_village(self, langeland):
        street(0), street(45000)
        house(100, 'Rudkøbing'), house(45100, 'Bagenkop')
        BaseImporter._merge_place_streets(langeland)
        assert len(live()) == 2

    def test_one_village_one_street_as_before(self, langeland):
        first, second = street(0), street(3000)
        house(100, 'Rudkøbing')
        BaseImporter._merge_place_streets(langeland)
        assert live() == [first]
        assert Street.objects.get(pk=second.pk).merged_into_id == first.pk

    def test_each_keeps_its_own_line(self, langeland):
        street(0), street(45000), street(45800, 100)
        house(100, 'Rudkøbing'), house(45100, 'Bagenkop')
        BaseImporter._merge_place_streets(langeland)
        assert sorted(s.lines.num_geom for s in live()) == [1, 2]


class TestHousesSharingASlug:
    def test_each_by_the_street_it_names(self):
        """kearns-5935: on no street, so its place's slug and its number."""
        names = ['South Dewdrops Drive', 'South Salem Avenue']
        for name in names:
            house(0, '', number='5935', street_name=name, slug='langeland-5935')
        Denorm.qualify_shared_address_slugs()
        assert sorted(Address.objects.values_list('slug', flat=True)) == [
            'langeland-5935-south-dewdrops-drive', 'langeland-5935-south-salem-avenue']

    def test_each_by_its_settlement_on_one_street(self):
        ostergade = street(0)
        house(100, 'Rudkøbing', on=ostergade, slug='ostergade-12')
        house(45100, 'Bagenkop', on=ostergade, slug='ostergade-12')
        Denorm.qualify_shared_address_slugs()
        assert sorted(Address.objects.values_list('slug', flat=True)) == [
            'ostergade-12-bagenkop', 'ostergade-12-rudkobing']

    def test_by_the_nearest_place_in_its_own_on_no_street(self):
        """Furth an der Triesting 46, in two of its villages."""
        furth = ['Furth an der Triesting', 'furth-an-der-triesting']
        for name, x in (('Aigen', 0), ('Steinhof', 7000)):
            Place.objects.create(name=name, slug=name.lower(), place=[furth],
                                 center=Point(10 + x * EAST, 55.0))
        for x in (50, 7050):
            house(x, 'Furth an der Triesting', number='46', street_name='', place=furth,
                  slug='furth-an-der-triesting-46')
        Denorm.qualify_shared_address_slugs()
        assert sorted(Address.objects.values_list('slug', flat=True)) == [
            'furth-an-der-triesting-46-aigen', 'furth-an-der-triesting-46-steinhof']

    def test_a_slug_of_its_own_is_left_alone(self):
        house(0, 'Rudkøbing', slug='ostergade-12')
        Denorm.qualify_shared_address_slugs()
        assert Address.objects.get().slug == 'ostergade-12'

    def test_what_it_shared_is_what_the_index_calls_it(self):
        ostergade = street(0)
        house(100, 'Rudkøbing', on=ostergade, slug='ostergade-12')
        house(45100, 'Bagenkop', on=ostergade, slug='ostergade-12')
        Denorm.qualify_shared_address_slugs()
        assert {a.slugify for a in Address.objects.all()} == {'ostergade-12'}

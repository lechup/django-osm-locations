"""
Remembering which row a merged row went into, so the one that stays carries
its OSM id.

Slugs of duplicates used to end in the OSM id -- ``station-road-25986049``.
A row merged since is left out of the index, and an old link to it is
followed to where it went by that id.
"""
import pytest
from django.contrib.gis.geos import LineString, MultiLineString, MultiPolygon, Point

from django_osm_locations.management.commands.importers.base import BaseImporter
from django_osm_locations.management.commands.locations_recalculate_denormalized_fields import (
    Command as Denorm,
)
from django_osm_locations.models import Address, Place, Street
from django_osm_locations.search.indexes import INDEXES

from .conftest import square

pytestmark = pytest.mark.django_db

WOLA = ['Wola', 'wola']


def street(name='Polna', way=1, x=20.0, place=WOLA):
    return Street.objects.create(name=name, way_osm_id=way, place=[place],
                                 lines=MultiLineString(LineString((x, 50.0), (x + 0.001, 50.0))))


def fresh(row):
    return type(row).objects.get(pk=row.pk)


class TestTheRowRemembers:
    def test_where_it_went(self):
        stays, goes = street(way=1), street(way=2, x=20.01)
        goes.mark_as_merged(into=stays)
        assert (fresh(goes).is_merged, fresh(goes).merged_into_id) == (True, stays.pk)

    def test_what_was_merged_into_it_goes_on_with_it(self):
        first, second, third = street(way=1), street(way=2, x=20.01), street(way=3, x=20.02)
        third.mark_as_merged(into=second)
        second.mark_as_merged(into=first)
        assert fresh(third).merged_into_id == first.pk

    def test_a_queryset_too(self):
        first, second, third = street(way=1), street(way=2, x=20.01), street(way=3, x=20.02)
        third.mark_as_merged(into=second)
        Street.objects.filter(pk=second.pk).mark_as_merged(into=first.pk)
        assert fresh(second).merged_into_id == fresh(third).merged_into_id == first.pk

    def test_merged_into_nothing_is_still_merged(self):
        """A place merged into a county is not a place any more, and there is no place it went to."""
        goes = street(way=2)
        goes.mark_as_merged()
        assert (fresh(goes).is_merged, fresh(goes).merged_into_id) == (True, None)


class TestTheMergesSayWhere:
    def test_the_import_merging_a_street_inside_a_border(self):
        place = Place.objects.create(name='Wola', border=MultiPolygon(square(19.9, 49.9, 0.3)))
        first, second = street(way=1), street(way=2, x=20.01)
        BaseImporter._merge_place_streets(place)
        assert fresh(second).merged_into_id == first.pk

    def test_the_import_merging_a_place(self):
        stays = Place.objects.create(name='Wola', border=MultiPolygon(square(19.9, 49.9, 0.3)),
                                     relation_osm_id=10)
        goes = Place.objects.create(name='Wola', center=Point(20.0, 50.0), node_osm_id=11)
        BaseImporter._merge_places()
        assert fresh(goes).merged_into_id == stays.pk

    def test_the_pieces_of_a_street(self):
        first, second = street(way=1), street(way=2, x=20.01)
        Denorm.merge_namesake_streets()
        assert fresh(second).merged_into_id == first.pk

    def test_and_what_the_import_had_merged_into_a_piece(self):
        first, second = street(way=1), street(way=2, x=20.01)
        earlier = street(way=3, x=20.02)
        earlier.mark_as_merged(into=second)
        Denorm.merge_namesake_streets()
        assert fresh(earlier).merged_into_id == first.pk

    def test_a_house_twice(self):
        polna = street(way=1)
        common = dict(house_number='5', street_name='Polna', place=[WOLA],
                      street=[['Polna', polna.slug]])
        building = Address.objects.create(name='Polna 5', way_osm_id=100,
                                          center=Point(20.0, 50.0), **common)
        shop = Address.objects.create(name='Polna 5', node_osm_id=200,
                                      center=Point(20.0001, 50.0), **common)
        Denorm.merge_repeated_houses()
        assert fresh(shop).merged_into_id == building.pk


class TestTheIndexCarriesTheirIds:
    def test_on_the_row_that_stays(self):
        stays = street(way=1)
        for way in (2, 3):
            street(way=way, x=20.0 + way / 100).mark_as_merged(into=stays)
        merged = INDEXES['streets'].merged_osm_ids([stays.pk])
        assert merged == {stays.pk: {'merged_node_ids': [], 'merged_way_ids': [2, 3],
                                     'merged_relation_ids': []}}
        osm = INDEXES['streets'].document(stays, merged[stays.pk])['osm']
        assert (osm['way_id'], osm['merged_way_ids']) == (1, [2, 3])

    def test_a_row_nothing_went_into_has_none(self):
        alone = street(way=1)
        assert INDEXES['streets'].merged_osm_ids([alone.pk]) == {}
        assert 'merged_way_ids' not in INDEXES['streets'].document(alone)['osm']

    def test_mapped(self):
        osm = INDEXES['streets'].properties['osm']['properties']
        assert {osm[key]['type'] for key in ('merged_node_ids', 'merged_way_ids',
                                             'merged_relation_ids')} == {'long'}

    def test_a_country_merges_nothing(self):
        assert INDEXES['countries'].merged_osm_ids([1]) == {}

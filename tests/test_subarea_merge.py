"""
Collapsing a division that is really the place inside it.

A Polish city with county rights exists three times over: as a place, as a
level 6 city-county and as a level 7 gmina, all sharing the city's name. Only
one of them should reach the search index.
"""
import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon

from django_osm_locations.management.commands.importers.poland import PolandImporter
from django_osm_locations.models import CountrySubarea, Place

pytestmark = pytest.mark.django_db


def box(x0, y0, x1, y1):
    return MultiPolygon(Polygon(((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))))


class TestMergingASubareaIntoItsPlace:
    def test_an_identical_border_still_merges(self):
        """The old rule, kept working: inland cities match exactly."""
        border = box(0, 0, 1, 1)
        Place.objects.create(name='Radom', border=border)
        subarea = CountrySubarea.objects.create(name='Radom', border=border)

        PolandImporter._merge_country_subareas(subarea)

        subarea.refresh_from_db()
        assert subarea.is_merged is True

    def test_a_larger_border_round_the_same_city_merges(self):
        """
        Gdańsk: the county takes in the bay and the port, 682 km² against the
        place's 396, so the borders are nowhere near equal.
        """
        Place.objects.create(name='Gdańsk', border=box(0, 0, 1, 1))
        subarea = CountrySubarea.objects.create(name='Gdańsk', border=box(-1, -1, 2, 2))

        PolandImporter._merge_country_subareas(subarea)

        subarea.refresh_from_db()
        assert subarea.is_merged is True

    def test_a_county_containing_a_differently_named_town_is_left_alone(self):
        """
        powiat gdański surrounds Gdańsk without being it. Without the name
        check, containment alone would swallow every county holding a town.
        """
        Place.objects.create(name='Gdańsk', border=box(0, 0, 1, 1))
        subarea = CountrySubarea.objects.create(name='powiat gdański', border=box(-1, -1, 2, 2))

        PolandImporter._merge_country_subareas(subarea)

        subarea.refresh_from_db()
        assert subarea.is_merged is False

    def test_a_place_of_the_same_name_somewhere_else_does_not_count(self):
        """There is a hamlet called Gdańsk in Warmia, 100km away."""
        Place.objects.create(name='Gdańsk', border=box(10, 10, 11, 11))
        subarea = CountrySubarea.objects.create(name='Gdańsk', border=box(0, 0, 1, 1))

        PolandImporter._merge_country_subareas(subarea)

        subarea.refresh_from_db()
        assert subarea.is_merged is False

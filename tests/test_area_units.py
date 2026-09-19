"""
Area is in square kilometres.

``Area()`` over a plain geometry column returns the SRID's units -- square
degrees for 4326 -- and the command divided that by a million as though it were
square metres. Warszawa came out as 6.8e-08 instead of 517.
"""
import inspect

import pytest
from django.contrib.gis.geos import MultiPolygon

from django_osm_locations.management.commands import locations_recalculate_areas_and_lengths
from django_osm_locations.models import Place

from .conftest import square

pytestmark = pytest.mark.django_db


def test_the_command_casts_to_geography_before_measuring():
    source = inspect.getsource(locations_recalculate_areas_and_lengths)
    assert 'geography=True' in source
    assert 'border_as_geography' in source


def test_a_stored_area_is_a_plausible_number_of_square_kilometres():
    """
    A one-degree square is on the order of ten thousand square kilometres at
    these latitudes -- not 1.0, and not 1e-06.
    """
    place = Place.objects.create(name='Big', border=MultiPolygon(square(19, 50, 1.0)))
    assert 1_000 < place.area < 100_000, place.area


def test_a_small_place_is_a_small_number_not_zero():
    place = Place.objects.create(name='Small', border=MultiPolygon(square(19, 50, 0.05)))
    assert 1 < place.area < 100, place.area


def test_area_scales_with_the_square_of_the_side():
    small = Place.objects.create(name='S', border=MultiPolygon(square(19, 50, 0.1)))
    large = Place.objects.create(name='L', border=MultiPolygon(square(21, 50, 0.2)))
    assert large.area == pytest.approx(small.area * 4, rel=0.05)


def test_no_border_means_no_area():
    assert Place(name='X')._presave_area() is None
    assert Place.objects.create(name='Nowhere').area == 0

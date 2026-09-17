"""Model behaviour that needs PostGIS."""
import pytest
from django.contrib.gis.geos import MultiPolygon

from django_osm_locations.models import Place, Street

from .conftest import square

pytestmark = pytest.mark.django_db


def test_slug_is_derived_from_name(place):
    assert place.slug == 'krakow'


def test_umlaut_slug_uses_unidecode(place_with_umlaut):
    assert place_with_umlaut.slug == 'munchen'


def test_presave_fills_derived_geometry_fields(place):
    assert place.center is not None
    assert place.area > 0
    assert len(place.bbox) == 4
    assert place.bbox == list(place.border.extent)


def test_bbox_falls_back_to_center_when_no_border():
    p = Place.objects.create(name='Wioska', center=MultiPolygon(square(5, 5)).point_on_surface)
    assert len(p.bbox) == 4
    assert p.bbox[0] < p.center[0] < p.bbox[2]


def test_population_coerces_osm_junk():
    """OSM population values include '~900' and '991 (31.12.2015)'."""
    for raw in ['~900', '991 (31.12.2015)', None, '']:
        p = Place(name='X', population=raw)
        p._presave_population()
        assert p.population == 0

    p = Place(name='X', population='1234')
    p._presave_population()
    assert p.population == 1234


def test_street_length_is_derived(street):
    assert street.length > 0


def test_mergeable_manager_filters(place):
    assert Place.objects.not_merged().count() == 1
    assert Place.objects.merged().count() == 0

    place.mark_as_merged()
    assert Place.objects.not_merged().count() == 0
    assert Place.objects.merged().count() == 1

    place.mark_as_not_merged()
    assert Place.objects.not_merged().count() == 1


def test_bbox_polygon_round_trips(place):
    assert place.bbox_polygon.extent == tuple(place.bbox)


def test_near_bbox_scales_with_km(place):
    near = place.near_bbox(km=10)
    wide = place.near_bbox(km=20)
    assert (wide[2] - wide[0]) > (near[2] - near[0])


def test_near_bbox_is_empty_without_center():
    assert Place(name='X').near_bbox() == []

"""
Trimming borders back to the coastline.

OSM administrative boundaries run out to sea: Gdańsk is 262 km² of land, but
the gmina relation measures 396 and the city-county 682. The polygon claims
open water, so the area is wrong, point-in-polygon matches things floating in
the bay, and a map draws a rectangle of sea.
"""
import pytest
from django.core.management.base import CommandError

from django_osm_locations.management.commands.locations_clip_borders_to_land import (
    SHRUNK_ENOUGH,
    SUBDIVIDE_VERTICES,
    Command as Clip,
)
from django_osm_locations.models import Country, CountrySubarea, Place, PlaceSubarea


class TestRefusesToGuess:
    def test_without_a_dataset_it_refuses(self, settings):
        settings.LOCATIONS_LAND_POLYGONS_TABLE = None
        with pytest.raises(CommandError, match='No land polygon table'):
            Clip().handle(types=[], table=None, resubdivide=False, dry_run=False)

    @pytest.mark.django_db
    def test_a_missing_table_is_reported(self, settings):
        settings.LOCATIONS_LAND_POLYGONS_TABLE = 'no_such_land_table'
        with pytest.raises(CommandError, match='No such table'):
            Clip().handle(types=[], table=None, resubdivide=False, dry_run=False)


class TestModelSelection:
    def test_defaults_to_every_bordered_model(self):
        assert Clip.selected_model_classes([]) == [
            Country, CountrySubarea, Place, PlaceSubarea
        ]

    def test_streets_have_no_border_to_clip(self):
        with pytest.raises(CommandError, match='not a bordered model'):
            Clip.selected_model_classes(['Street'])


class TestSubdividing:
    """
    The dataset's largest polygon is Eurasia-Africa at 7.2 million vertices.
    Intersecting a village against that is slow whatever the index says, and
    subdividing took clipping Poland's 2,876 subareas from hours to 12s.
    """

    def test_tiles_are_small(self):
        assert SUBDIVIDE_VERTICES <= 256

    def test_it_builds_a_separate_table(self):
        """The original is left alone; the tiles live beside it."""
        assert Clip.subdivided_name('locations_land') == 'locations_land_subdivided'

    def test_the_tiles_are_indexed(self):
        statements = ' '.join(Clip.sql_for_subdivide('locations_land'))
        assert 'USING gist' in statements
        assert 'ST_Subdivide(geom, {})'.format(SUBDIVIDE_VERTICES) in statements


class TestClipSql:
    def test_polygons_only_are_kept(self):
        """
        Where a border grazes a tile edge the intersection is a point or a
        line, and a union holding those is a GeometryCollection the column
        rejects outright.
        """
        assert 'ST_CollectionExtract' in Clip.sql_for_clip('place', 'locations_land')

    def test_it_clips_against_the_tiles_not_the_originals(self):
        sql = Clip.sql_for_clip('place', 'locations_land')
        assert 'locations_land_subdivided' in sql

    def test_pieces_are_reassembled(self):
        sql = Clip.sql_for_clip('place', 'locations_land')
        assert 'ST_Union' in sql and 'GROUP BY loc.id' in sql


class TestApplySql:
    def test_only_shrunken_borders_are_written(self):
        """
        Inland borders are the vast majority -- 2,810 of Poland's 2,876
        subareas -- and rewriting them would only add collinear points along
        the tile seams.
        """
        assert 'ST_Area(loc.border::geography) * %s' in Clip.sql_for_apply('place')

    def test_the_threshold_is_a_shrink_not_a_change(self):
        assert 0.99 < SHRUNK_ENOUGH < 1.0

    def test_area_is_recomputed_from_the_clipped_border(self):
        """No second column: area from a clipped border is the land area."""
        assert 'area   = ST_Area(c.border::geography) / 1000000' in Clip.sql_for_apply('place')

    def test_the_bbox_is_maintained(self):
        """Presave hooks do not run for a bulk UPDATE."""
        assert 'bbox   = ARRAY[ST_XMin' in Clip.sql_for_apply('place')

    def test_a_centre_left_in_the_water_is_moved(self):
        """
        A centre outside its own border breaks the point-in-polygon pass that
        builds ancestry, and a harbour's point-on-surface can easily sit in
        water the clip has just removed.
        """
        sql = Clip.sql_for_apply('place')
        assert 'ST_Intersects(c.border, loc.center)' in sql
        assert 'ST_PointOnSurface(c.border)' in sql

    def test_a_centre_already_on_land_is_left_alone(self):
        assert 'THEN loc.center' in Clip.sql_for_apply('place')

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
    MERGE_VERTICES_CLOSER_THAN,
    SHRUNK_ENOUGH,
    SIMPLIFY_DEGREES,
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

    def test_the_vertex_limit_reaches_subdivide(self):
        statements = ' '.join(Clip.sql_for_subdivide('locations_land'))
        assert 'ST_Subdivide(' in statements
        assert ', {})'.format(SUBDIVIDE_VERTICES) in statements


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


class TestOnlyTheCoastIsSimplified:
    """
    A clipped border follows the coast on one side and the administrative
    boundary on the other. Simplifying the border would move both, and an
    inland boundary is shared with the neighbouring unit -- move it and
    adjacent polygons stop tiling, opening gaps and overlaps exactly where
    point-in-polygon has to decide.
    """

    def test_simplification_is_applied_to_the_land_not_the_border(self):
        statements = ' '.join(Clip.sql_for_subdivide('locations_land'))
        assert 'ST_SimplifyPreserveTopology(geom' in statements
        assert 'ST_SimplifyPreserveTopology' not in Clip.sql_for_apply('place')
        assert 'ST_SimplifyPreserveTopology' not in Clip.sql_for_clip('place', 'locations_land')

    def test_it_simplifies_before_subdividing(self):
        """
        Subdivision cuts along arbitrary lines; simplifying those afterwards
        would pull neighbouring tiles apart and leave slivers of unclaimed
        sea between them.
        """
        create = [s for s in Clip.sql_for_subdivide('locations_land') if 'CREATE TABLE' in s][0]
        assert create.index('ST_SimplifyPreserveTopology') > create.index('ST_Subdivide')

    def test_the_tolerance_is_metres_not_kilometres(self):
        """10m against a border already 0.08% off the published area."""
        assert 0 < SIMPLIFY_DEGREES <= 0.0005

    def test_topology_is_preserved_rather_than_plain_simplify(self):
        """ST_Simplify can invalidate a polygon outright."""
        statements = ' '.join(Clip.sql_for_subdivide('locations_land'))
        assert 'ST_SimplifyPreserveTopology' in statements


class TestGeometryIsWritableToTheIndex:
    """
    Intersecting against tiles puts new vertices on the seams, and two of them
    can land closer together than the seven decimal places GEOS writes GeoJSON
    with. PostGIS keeps them as distinct points; the serialised shape has the
    same coordinate twice, and OpenSearch refuses it -- "duplicate consecutive
    coordinates". One such vertex failed an entire reindex, and 293 stored
    geometries had them.
    """

    def test_near_duplicate_vertices_are_merged_on_write(self):
        assert 'ST_RemoveRepeatedPoints' in Clip.sql_for_apply('place')

    def test_the_tolerance_is_below_what_geojson_can_express(self):
        """GEOS writes seven decimals; anything finer cannot survive it."""
        assert MERGE_VERTICES_CLOSER_THAN <= 0.0000001

    def test_the_tolerance_is_not_large_enough_to_move_a_coastline(self):
        """About a centimetre -- far under the 10m simplification."""
        assert MERGE_VERTICES_CLOSER_THAN < SIMPLIFY_DEGREES / 100


class TestTheTilesAreACacheWithAKey:
    """
    The subdivided tiles carry the simplification, so they are a cache keyed by
    the tolerance -- and for a while they had no key. The 10m simplification was
    added, a clip ran for two hours against tiles built before it, and the
    United Kingdom came back with the same 2,238,352 vertices it went in with,
    because ensure_subdivided found a table and returned.
    """

    def test_the_build_stamps_the_tolerance_on_the_table(self):
        statements = Clip.sql_for_subdivide('locations_land')
        stamped = [s for s in statements if 'COMMENT ON TABLE' in s]
        assert len(stamped) == 1
        assert 'simplify_degrees={}'.format(SIMPLIFY_DEGREES) in stamped[0]

    def test_the_stamp_names_the_subdivided_table_not_the_source(self):
        statements = Clip.sql_for_subdivide('locations_land')
        stamped = next(s for s in statements if 'COMMENT ON TABLE' in s)
        assert 'locations_land_subdivided' in stamped

    @pytest.mark.parametrize('comment,expected', [
        ('simplify_degrees=0.0001', '0.0001'),
        ('simplify_degrees=1e-05', '1e-05'),
        ('', None),
        (None, None),
        ('something else entirely', None),
    ])
    def test_reading_the_stamp_back(self, comment, expected):
        class Cursor:
            def execute(self, sql, params=None):
                pass

            def fetchone(self):
                return (comment,)

        assert Clip.subdivided_tolerance(Cursor(), 'locations_land_subdivided') == expected

    def test_tiles_from_before_the_stamp_are_treated_as_stale(self):
        """
        An unstamped table is exactly the case that caused this: tiles built
        before the tolerance existed. None must not compare equal to the
        current tolerance.
        """
        class Cursor:
            def execute(self, sql, params=None):
                pass

            def fetchone(self):
                return (None,)

        built_with = Clip.subdivided_tolerance(Cursor(), 'locations_land_subdivided')
        assert built_with != str(SIMPLIFY_DEGREES)

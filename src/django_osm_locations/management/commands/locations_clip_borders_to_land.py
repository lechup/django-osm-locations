"""
Trim borders back to the coastline.

OSM administrative boundaries run out to sea, so a coastal border covers water
it has no business covering. Gdansk is 262 km2 of land; the gmina relation
measures 396 and the city-county 682, the difference being the Bay of Gdansk.
That is not only a wrong ``area`` -- it is a polygon that claims open water, so
point-in-polygon matches things floating in it and a map draws a rectangle of
sea.

Clipping fixes all three at once and needs no second column: an ``area``
computed from a clipped border *is* the land area.

The coastline is not in a Geofabrik extract in any usable form -- extracts
carry ``natural=coastline`` ways, and assembling those into land polygons is
what osmcoastline exists to do -- so this reads polygons loaded beforehand by
``locations_load_land_polygons``::

    make coastline                       # download, unpack and load
    LOCATIONS_LAND_POLYGONS_TABLE = 'locations_land'

Two things make it fast enough to be worth running. The dataset's largest
polygon is Eurasia-Africa at 7.2 million vertices, and intersecting a village
against that is slow however good the index is, so the table is subdivided once
into tiles of at most ``SUBDIVIDE_VERTICES``; clipping every Polish country
subarea then takes twelve seconds rather than hours. And a border is only
written back when clipping actually removed something, so the inland majority
-- 2,810 of Poland's 2,876 subareas -- keeps its geometry untouched rather than
acquiring collinear points along the tile seams.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from .. import Country, CountrySubarea, Place, PlaceSubarea

#: Tiles bigger than this cost more to intersect than they save.
SUBDIVIDE_VERTICES = 256

#: Degrees of tolerance for simplifying the coastline, roughly 10 metres.
#:
#: Applied to the land polygons, not to the borders, and that distinction is
#: the point: a clipped border follows the coast on one side and the
#: administrative boundary on the other. Simplifying the border would move
#: both, and an inland boundary is shared with the neighbouring unit -- move
#: it and adjacent polygons stop tiling, opening gaps and overlaps exactly
#: where point-in-polygon has to decide. Simplifying the input instead leaves
#: every inland edge untouched and bit-identical.
#:
#: 10m takes the United Kingdom's clipped border from 2,238,356 vertices to
#: 522,825 and its area from 244,583 km2 to 244,579 -- an error of 0.002%,
#: against the 0.08% the unsimplified dataset already carries versus the
#: published 244,376.
SIMPLIFY_DEGREES = 0.0001

#: Vertices closer than this are merged, in degrees -- about a centimetre.
#:
#: Intersecting against tiles puts new vertices on the seams, and two of them
#: can land closer together than the seven decimal places GEOS writes GeoJSON
#: with. PostGIS keeps them as distinct points; the serialised geometry has
#: the same coordinate twice, and OpenSearch rejects the shape outright with
#: "duplicate consecutive coordinates". One such vertex fails a whole bulk
#: request, so the geometry is cleaned where it is made.
MERGE_VERTICES_CLOSER_THAN = 0.0000001

#: How much smaller the clipped border must be before it is written back.
#: Well above the noise of reassembling a polygon from tiles, well below any
#: real stretch of coast.
SHRUNK_ENOUGH = 0.999

#: ...or how much simpler, at the same area, for the same reason.
#:
#: Shrinkage alone was the whole test, and it made an already-clipped border
#: impossible to improve: re-clipping against a finer or coarser coastline
#: changes the outline without changing the area, the area test saw no change,
#: and the simpler geometry was computed and thrown away. Measured on the
#: United Kingdom, whose border is nearly all coast: the land in its bounding
#: box carries 10,274,780 vertices raw and 677,026 once simplified to 10m, a
#: 93% reduction that the border never received. It sat at 2,238,352 vertices
#: through two clips.
SIMPLER_ENOUGH = 0.95

#: How much the area may move for a rewrite that is only a simplification.
#: Simplifying the coastline can push the line either way, so the same land
#: does not mean the identical area -- but it does mean a very close one, and
#: anything further out is a geometry to reject rather than accept quietly.
SAME_LAND_WITHIN = 0.001


def land_polygons_table():
    return getattr(settings, 'LOCATIONS_LAND_POLYGONS_TABLE', None)


class Command(BaseCommand):
    help = (
        "Clip every border to the coastline, using the land polygons named by "
        "LOCATIONS_LAND_POLYGONS_TABLE. Borders that are entirely inland are "
        "left exactly as they are."
    )

    MODEL_CLASSES = [Country, CountrySubarea, Place, PlaceSubarea]

    def add_arguments(self, parser):
        parser.add_argument(
            'types', nargs='*',
            help='Model names to clip. Defaults to all of them.',
        )
        parser.add_argument(
            '--table', default=None,
            help='Land polygon table, overriding LOCATIONS_LAND_POLYGONS_TABLE.',
        )
        parser.add_argument(
            '--resubdivide', action='store_true', default=False,
            help='Rebuild the subdivided tiles even if they are already there.',
        )
        parser.add_argument(
            '--dry-run', action='store_true', default=False,
            help='Report what would change without writing anything.',
        )

    @classmethod
    def selected_model_classes(cls, names):
        if not names:
            return list(cls.MODEL_CLASSES)
        wanted = {name.lower() for name in names}
        known = {model.__name__.lower(): model for model in cls.MODEL_CLASSES}
        unknown = wanted - set(known)
        if unknown:
            raise CommandError('not a bordered model: {}. Choose from {}.'.format(
                ', '.join(sorted(unknown)),
                ', '.join(model.__name__ for model in cls.MODEL_CLASSES),
            ))
        return [known[name] for name in sorted(wanted)]

    @staticmethod
    def subdivided_name(table):
        return '{}_subdivided'.format(table)

    @classmethod
    def sql_for_subdivide(cls, table):
        """
        Split the land polygons into tiles small enough to intersect cheaply.

        ST_Subdivide keeps the covered area identical, so clipping against the
        tiles gives the same answer as clipping against the originals; it only
        changes how much geometry each intersection has to read.
        """
        subdivided = cls.subdivided_name(table)
        return [
            'DROP TABLE IF EXISTS "{}"'.format(subdivided),
            # Simplify before subdividing, never after: subdivision cuts along
            # arbitrary lines and simplifying those would pull neighbouring
            # tiles apart, leaving slivers of unclaimed sea between them.
            'CREATE TABLE "{}" AS SELECT ST_Subdivide('
            'ST_SimplifyPreserveTopology(geom, {}), {})::geometry(Polygon, 4326) '
            'AS geom FROM "{}"'.format(
                subdivided, SIMPLIFY_DEGREES, SUBDIVIDE_VERTICES, table),
            'CREATE INDEX "{0}_gist" ON "{0}" USING gist (geom)'.format(subdivided),
            'ANALYZE "{}"'.format(subdivided),
            # Stamp the tolerance on the table. Without it the tiles are a
            # cache with no key: changing SIMPLIFY_DEGREES leaves the old tiles
            # in place, ensure_subdivided finds a table and returns, and the
            # clip silently uses the previous tolerance. That happened -- the
            # 10m simplification was added, a clip ran for two hours against
            # tiles built before it, and the United Kingdom came back with the
            # same 2,238,352 vertices it had gone in with.
            "COMMENT ON TABLE \"{}\" IS 'simplify_degrees={}'".format(
                subdivided, SIMPLIFY_DEGREES),
        ]

    @classmethod
    def sql_for_clip(cls, model_name, table):
        """
        Build the clipped geometry for every border, into a staging table.

        ST_CollectionExtract takes the polygons only: where a border merely
        grazes a tile edge the intersection is a point or a line, and a union
        containing those is a GeometryCollection the column will not accept.
        """
        return """
            DROP TABLE IF EXISTS "{staging}";
            CREATE TEMPORARY TABLE "{staging}" AS
            SELECT loc.id,
                   ST_Multi(ST_CollectionExtract(
                       ST_Union(ST_Intersection(loc.border, land.geom)), 3
                   ))::geometry(MultiPolygon, 4326) AS border
            FROM "locations_{model_name}" loc
            JOIN "{subdivided}" land ON loc.border && land.geom
            WHERE loc.border IS NOT NULL
            GROUP BY loc.id, loc.border;
        """.format(
            staging=cls.staging_name(model_name),
            model_name=model_name,
            subdivided=cls.subdivided_name(table),
        )

    @staticmethod
    def staging_name(model_name):
        return 'clipped_{}'.format(model_name)

    #: The two reasons to write a border back: it lost sea, or it describes the
    #: same land with materially fewer vertices. Kept in one place because the
    #: apply and the count must not disagree about what counts as a change.
    CHANGED_ENOUGH = """
        (
              ST_Area(c.border::geography) < ST_Area(loc.border::geography) * {shrunk}
           OR (
                  ST_Area(c.border::geography)
                      BETWEEN ST_Area(loc.border::geography) * (1 - {same_land})
                          AND ST_Area(loc.border::geography) * (1 + {same_land})
              AND ST_NPoints(c.border) < ST_NPoints(loc.border) * {simpler}
              )
        )
    """

    @classmethod
    def changed_enough(cls):
        return cls.CHANGED_ENOUGH.format(
            shrunk=SHRUNK_ENOUGH, simpler=SIMPLER_ENOUGH, same_land=SAME_LAND_WITHIN)

    @classmethod
    def sql_for_apply(cls, model_name):
        """
        Write back the borders the clip shrank, and those it only simplified.

        ``center`` is recomputed where the old one is no longer inside -- a
        harbour's point-on-surface can easily land in water the clip has just
        removed, and a centre outside its own border breaks the point-in-
        polygon pass that builds ancestry. ``bbox`` is maintained here too
        because the model's presave hooks do not run for a bulk UPDATE.
        """
        return """
            UPDATE "locations_{model_name}" AS loc SET
                border = ST_RemoveRepeatedPoints(c.border, {merge}),
                area   = ST_Area(c.border::geography) / 1000000,
                bbox   = ARRAY[ST_XMin(c.border), ST_YMin(c.border),
                               ST_XMax(c.border), ST_YMax(c.border)],
                center = CASE WHEN ST_Intersects(c.border, loc.center) THEN loc.center
                              ELSE ST_PointOnSurface(c.border) END
            FROM "{staging}" c
            WHERE loc.id = c.id AND {changed};
        """.format(model_name=model_name, staging=cls.staging_name(model_name),
                   merge=MERGE_VERTICES_CLOSER_THAN, changed=cls.changed_enough())

    @classmethod
    def sql_for_count(cls, model_name):
        return """
            SELECT count(*) FROM "{staging}" c
            JOIN "locations_{model_name}" loc ON loc.id = c.id
            WHERE {changed};
        """.format(staging=cls.staging_name(model_name), model_name=model_name,
                   changed=cls.changed_enough())

    @classmethod
    def subdivided_tolerance(cls, cursor, subdivided):
        """
        The tolerance the existing tiles were built with, or None.

        None covers both "no tiles" and "tiles from before they were stamped",
        and both want rebuilding.
        """
        cursor.execute(
            "SELECT obj_description(to_regclass(%s), 'pg_class')", [subdivided])
        row = cursor.fetchone()
        comment = row[0] if row else None
        if not comment or not comment.startswith('simplify_degrees='):
            return None
        return comment.split('=', 1)[1]

    def ensure_subdivided(self, table, rebuild):
        subdivided = self.subdivided_name(table)
        with connection.cursor() as cursor:
            cursor.execute('SELECT to_regclass(%s)', [subdivided])
            exists = cursor.fetchone()[0] is not None
            built_with = self.subdivided_tolerance(cursor, subdivided) if exists else None
            stale = built_with != str(SIMPLIFY_DEGREES)
            if exists and not rebuild and not stale:
                return
            if exists and stale and not rebuild:
                self.stdout.write(
                    'tiles were built with tolerance {}, not {} -- rebuilding, '
                    'or the clip would quietly use the old one'.format(
                        built_with or 'unknown', SIMPLIFY_DEGREES))
            self.stdout.write('subdividing {} into {}-vertex tiles (once, slow)...'.format(
                table, SUBDIVIDE_VERTICES))
            for statement in self.sql_for_subdivide(table):
                cursor.execute(statement)

    def handle(self, *args, **options):
        table = options.get('table') or land_polygons_table()
        if not table:
            raise CommandError(
                'No land polygon table. Set LOCATIONS_LAND_POLYGONS_TABLE, or pass '
                '--table. See this command\'s module docstring for where to get one.'
            )
        with connection.cursor() as cursor:
            cursor.execute('SELECT to_regclass(%s)', [table])
            if cursor.fetchone()[0] is None:
                raise CommandError('No such table: {}'.format(table))

        model_classes = self.selected_model_classes(options.get('types'))
        dry_run = options.get('dry_run')
        self.ensure_subdivided(table, options.get('resubdivide'))

        for model_class in model_classes:
            model_name = model_class.__name__.lower()
            self.stdout.write('{} - clipping borders to land...'.format(model_class.__name__))
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(self.sql_for_clip(model_name, table))
                    cursor.execute(self.sql_for_count(model_name))
                    changed = cursor.fetchone()[0]
                    if dry_run:
                        self.stdout.write('  {} would be clipped'.format(changed))
                        continue
                    cursor.execute(self.sql_for_apply(model_name))
                    self.stdout.write('  {} clipped'.format(changed))

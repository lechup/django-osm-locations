"""
Load a coastline dataset for locations_clip_borders_to_land.

The usual instruction for getting a shapefile into PostGIS is ogr2ogr, which
comes from GDAL's command line tools -- ``apt install gdal-bin``, and not
reliably installable from PyPI. GeoDjango needs the GDAL *library* but not
those tools, so telling somebody to run ogr2ogr is asking them to install a
system package for one import. pyogrio bundles GDAL in a wheel and hands back
WKB directly, which is both fewer moving parts and faster.

What to load is land-polygons-complete-4326 from osmdata.openstreetmap.de:
about 900MB zipped, 832,306 polygons, 78.9 million vertices. It is the land
side of the OSM coastline, already assembled -- the job osmcoastline exists to
do, which a Geofabrik extract does not give you.

    make coastline                       # download, unpack and load
    python -m django locations_load_land_polygons path/to/land_polygons.shp
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

#: Streamed in batches: the whole thing will not fit comfortably in memory.
CHUNK = 25000

DOWNLOAD_URL = (
    'https://osmdata.openstreetmap.de/download/land-polygons-complete-4326.zip'
)


def _load_pyogrio():
    try:
        import pyogrio
    except ImportError:
        raise CommandError(
            'pyogrio is needed to read the shapefile: pip install '
            '"django-osm-locations[coastline]". Alternatively load the file '
            'with ogr2ogr (apt install gdal-bin) into the table named by '
            'LOCATIONS_LAND_POLYGONS_TABLE.'
        )
    return pyogrio


class Command(BaseCommand):
    help = (
        "Load land polygons into the table named by "
        "LOCATIONS_LAND_POLYGONS_TABLE, for locations_clip_borders_to_land. "
        "Get the shapefile from {}".format(DOWNLOAD_URL)
    )

    def add_arguments(self, parser):
        parser.add_argument('shapefile', help='Path to land_polygons.shp')
        parser.add_argument(
            '--table', default=None,
            help='Table to load into, overriding LOCATIONS_LAND_POLYGONS_TABLE.',
        )

    @staticmethod
    def sql_for_table(table):
        return [
            'DROP TABLE IF EXISTS "{}"'.format(table),
            'CREATE TABLE "{}" (id serial primary key, '
            'geom geometry(MultiPolygon, 4326))'.format(table),
        ]

    @staticmethod
    def sql_for_index(table):
        return [
            'CREATE INDEX "{0}_geom_gist" ON "{0}" USING gist (geom)'.format(table),
            'ANALYZE "{}"'.format(table),
        ]

    @staticmethod
    def sql_for_insert(table):
        """
        ST_Multi because the dataset mixes Polygon and MultiPolygon, and one
        column cannot hold both.
        """
        return 'INSERT INTO "{}" (geom) VALUES %s'.format(table)

    def handle(self, *args, **options):
        pyogrio = _load_pyogrio()
        from psycopg2.extras import execute_values

        path = options['shapefile']
        table = options.get('table') or getattr(
            settings, 'LOCATIONS_LAND_POLYGONS_TABLE', None
        )
        if not table:
            raise CommandError(
                'No table to load into. Set LOCATIONS_LAND_POLYGONS_TABLE or pass --table.'
            )

        try:
            total = pyogrio.read_info(path)['features']
        except Exception as error:
            raise CommandError('Cannot read {}: {}'.format(path, error))
        self.stdout.write('{:,} polygons in {}'.format(total, path))

        started = time.time()
        with connection.cursor() as cursor:
            for statement in self.sql_for_table(table):
                cursor.execute(statement)

            loaded = 0
            while loaded < total:
                # raw.read returns (meta, index, geometry, field_data): WKB
                # straight off the file, with no geopandas in the way
                _meta, _index, geometry, _fields = pyogrio.raw.read(
                    path, skip_features=loaded, max_features=CHUNK, columns=[],
                )
                if geometry is None or len(geometry) == 0:
                    break
                execute_values(
                    cursor,
                    self.sql_for_insert(table),
                    [(memoryview(wkb),) for wkb in geometry if wkb is not None],
                    template='(ST_Multi(ST_SetSRID(ST_GeomFromWKB(%s), 4326)))',
                    page_size=2000,
                )
                loaded += len(geometry)
                self.stdout.write('  {:>8,} / {:,}'.format(loaded, total))

            self.stdout.write('indexing...')
            for statement in self.sql_for_index(table):
                cursor.execute(statement)

        self.stdout.write('loaded {:,} polygons in {:.0f}s. Now run '
                          'locations_clip_borders_to_land.'.format(
                              loaded, time.time() - started))

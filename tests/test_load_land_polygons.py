"""
Getting a coastline into PostGIS without apt-installing GDAL's tools.

The usual instruction is ogr2ogr, which comes from gdal-bin. GeoDjango needs
the GDAL library but not those tools, so that asks somebody to install a
system package for one import.
"""
import pytest
from django.core.management.base import CommandError

from django_osm_locations.management.commands.locations_load_land_polygons import (
    CHUNK,
    DOWNLOAD_URL,
    Command as Load,
)


class TestTableSql:
    def test_the_column_holds_multipolygons_in_4326(self):
        statements = ' '.join(Load.sql_for_table('locations_land'))
        assert 'geometry(MultiPolygon, 4326)' in statements

    def test_it_replaces_rather_than_appends(self):
        """Loading twice should not double the coastline."""
        assert 'DROP TABLE IF EXISTS' in Load.sql_for_table('locations_land')[0]

    def test_the_index_is_gist(self):
        """Everything downstream finds tiles by `&&`."""
        assert 'USING gist' in ' '.join(Load.sql_for_index('locations_land'))

    def test_it_analyzes_so_the_planner_knows(self):
        assert any('ANALYZE' in s for s in Load.sql_for_index('locations_land'))

    def test_it_inserts_into_the_named_table(self):
        assert 'INSERT INTO "locations_land"' in Load.sql_for_insert('locations_land')


class TestItStreams:
    def test_it_does_not_read_832306_polygons_at_once(self):
        assert 0 < CHUNK <= 50000


class TestRefusals:
    @pytest.mark.django_db
    def test_a_missing_file_is_reported_clearly(self, settings, tmp_path):
        settings.LOCATIONS_LAND_POLYGONS_TABLE = 'locations_land'
        pytest.importorskip('pyogrio')
        with pytest.raises(CommandError, match='Cannot read'):
            Load().handle(shapefile=str(tmp_path / 'nope.shp'), table=None)

    def test_the_download_url_is_the_complete_4326_build(self):
        """Split builds are tiled; their boundaries are not coastline."""
        assert 'land-polygons-complete-4326' in DOWNLOAD_URL

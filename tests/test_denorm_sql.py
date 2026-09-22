"""
How the denormalisation reaches a border.

It used to send one: ``obj.border.hexewkb`` loaded the geometry into Python,
hex-encoded it to roughly twice its size, and shipped it back for the server
to parse again -- once per descendant table. That was survivable while borders
were small. Clipping them to the coastline took the United Kingdom's border
from 67,000 vertices to 2.2 million, so a single statement carried a 72MB
string four times over, and the whole hierarchy came to about a gigabyte of
transient strings per pass. systemd-oomd killed the user session under the
resulting memory pressure.
"""
import pytest

from django_osm_locations.management.commands.locations_recalculate_denormalized_fields import (
    Command as Denorm,
)
from django_osm_locations.models import Country, CountrySubarea, Place, PlaceSubarea


class TestTheBorderStaysInTheDatabase:
    def test_the_border_is_read_not_sent(self):
        sql = Denorm.get_sql_for_update_denorm_fields('street', 'country', 'locations_country')
        assert 'SELECT border FROM "locations_country" WHERE id = %s' in sql

    def test_no_geometry_is_decoded_from_a_parameter(self):
        """ST_GeomFromEWKB(decode(...)) is the shape of the old bug."""
        sql = Denorm.get_sql_for_update_denorm_fields('street', 'country', 'locations_country')
        assert 'ST_GeomFromEWKB' not in sql
        assert 'decode(' not in sql

    def test_it_still_matches_on_the_centre(self):
        sql = Denorm.get_sql_for_update_denorm_fields('street', 'country', 'locations_country')
        assert 'ST_Intersects(center,' in sql

    def test_exactly_three_parameters(self):
        """name, slug, ancestor id -- all small."""
        sql = Denorm.get_sql_for_update_denorm_fields('street', 'country', 'locations_country')
        assert sql.count('%s') == 3

    @pytest.mark.parametrize('model_class,table', [
        (Country, 'locations_country'),
        (CountrySubarea, 'locations_countrysubarea'),
        (Place, 'locations_place'),
        (PlaceSubarea, 'locations_placesubarea'),
    ])
    def test_every_ancestor_table_is_addressable(self, model_class, table):
        """The table name comes from _meta, never from data."""
        assert model_class._meta.db_table == table
        sql = Denorm.get_sql_for_update_denorm_fields('street', 'country', table)
        assert '"{}"'.format(table) in sql


@pytest.mark.django_db
class TestItStillWorks:
    def test_a_child_inside_the_border_gets_the_ancestor(self):
        from django.contrib.gis.geos import MultiPolygon, Point, Polygon
        from django.db import connection

        country = Country.objects.create(
            name='Testland', slug='testland', iso2='TT', iso3='TTT', un=999,
            border=MultiPolygon(Polygon(((0, 0), (10, 0), (10, 10), (0, 10), (0, 0)))),
        )
        inside = Place.objects.create(name='In', slug='in', center=Point(5, 5), place=[['', '']])
        outside = Place.objects.create(name='Out', slug='out', center=Point(50, 50), place=[['', '']])
        Place.objects.filter(pk__in=[inside.pk, outside.pk]).update(country=[['', '']])

        sql = Denorm.get_sql_for_update_denorm_fields('place', 'country', 'locations_country')
        with connection.cursor() as cursor:
            cursor.execute(sql, [country.name, country.slug, country.id])

        inside.refresh_from_db()
        outside.refresh_from_db()
        assert ['Testland', 'testland'] in inside.country
        assert ['Testland', 'testland'] not in outside.country

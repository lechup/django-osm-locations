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
        assert 'SELECT border FROM "locations_country" WHERE id = %(id)s' in sql

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
        import re

        sql = Denorm.get_sql_for_update_denorm_fields('street', 'country', 'locations_country')
        assert set(re.findall(r'%\((\w+)\)s', sql)) == {'name', 'slug', 'id'}
        assert '%s' not in sql

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
            cursor.execute(sql, {'name': country.name, 'slug': country.slug, 'id': country.id})

        inside.refresh_from_db()
        outside.refresh_from_db()
        assert ['Testland', 'testland'] in inside.country
        assert ['Testland', 'testland'] not in outside.country


def square(x0, y0, x1, y1):
    from django.contrib.gis.geos import MultiPolygon, Polygon

    return MultiPolygon(Polygon(((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))))


@pytest.mark.django_db
class TestALevelPushedOntoItself:
    """
    Every row's centre lies in its own border, and a centre can lie in
    something smaller: Kazimierz was its own ancestor, and gmina Zabierzów an
    ancestor of the powiat around it.
    """

    @pytest.fixture
    def powiat_and_gmina(self):
        from django.contrib.gis.geos import Point

        powiat = CountrySubarea.objects.create(
            name='powiat krakowski', slug='powiat-krakowski', border=square(0, 0, 10, 10),
            center=Point(2, 2), area=100.0, countrysubarea=[])
        # the powiat's centre falls inside the gmina
        gmina = CountrySubarea.objects.create(
            name='gmina Zabierzów', slug='gmina-zabierzow', border=square(1, 1, 3, 3),
            center=Point(2, 2), area=4.0, countrysubarea=[])
        return powiat, gmina

    def push(self, ancestor):
        from django.db import connection

        sql = Denorm.get_sql_for_update_denorm_fields(
            'countrysubarea', 'countrysubarea', 'locations_countrysubarea')
        with connection.cursor() as cursor:
            cursor.execute(sql, {'name': ancestor.name, 'slug': ancestor.slug, 'id': ancestor.id})

    def test_nothing_is_its_own_ancestor(self, powiat_and_gmina):
        powiat, gmina = powiat_and_gmina
        self.push(powiat)
        powiat.refresh_from_db()
        assert ['powiat krakowski', 'powiat-krakowski'] not in (powiat.countrysubarea or [])

    def test_the_larger_one_is_the_ancestor(self, powiat_and_gmina):
        powiat, gmina = powiat_and_gmina
        self.push(powiat)
        gmina.refresh_from_db()
        assert ['powiat krakowski', 'powiat-krakowski'] in gmina.countrysubarea

    def test_a_smaller_one_never_is(self, powiat_and_gmina):
        powiat, gmina = powiat_and_gmina
        self.push(gmina)
        powiat.refresh_from_db()
        assert ['gmina Zabierzów', 'gmina-zabierzow'] not in (powiat.countrysubarea or [])

    def test_another_table_is_unaffected(self):
        assert Denorm.sql_for_same_level('street', 'locations_countrysubarea') == ''


@pytest.mark.django_db
class TestAMergedDuplicateIsNoAncestor:
    def test_a_district_drawn_twice_is_pushed_once(self):
        from django.contrib.gis.geos import Point

        from django_osm_locations.models import Street

        for merged in (False, True):
            PlaceSubarea.objects.create(
                name='Stare Miasto', slug='stare-miasto', border=square(0, 0, 10, 10),
                center=Point(5, 5), area=100.0, is_merged=merged)
        street = Street.objects.create(name='Szeroka', slug='szeroka', center=Point(5, 5),
                                       placesubarea=[])
        Denorm.update_denorm_fields_for_model_class(PlaceSubarea)
        street.refresh_from_db()
        assert street.placesubarea == [['Stare Miasto', 'stare-miasto']]

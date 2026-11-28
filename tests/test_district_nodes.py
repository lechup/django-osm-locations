"""
Districts drawn as points.

Dorstfeld in Dortmund, Löbtau in Dresden, Bałuty in Łódź: ``place=suburb``
nodes with no border of their name, which the importers passed over, so the
districts people name were not in the data. They are place subareas now; the
ones that only repeat a border are folded into it.
"""
import pytest
from django.contrib.gis.geos import MultiPolygon, Point

from django_osm_locations.management.commands.importers.base import BaseImporter
from django_osm_locations.management.commands.importers.germany import GermanyImporter
from django_osm_locations.management.commands.importers.poland import PolandImporter
from django_osm_locations.models import Place, PlaceSubarea

from .conftest import square


class TestWhichNodes:
    @pytest.mark.parametrize('importer', [BaseImporter, GermanyImporter, PolandImporter])
    @pytest.mark.parametrize('tags,expected', [
        ({'place': 'suburb', 'name': 'Dorstfeld'}, True),
        ({'place': 'quarter', 'name': 'Czechów'}, True),
        ({'place': 'neighbourhood', 'name': 'Czechów Dolny'}, False),
        ({'place': 'suburb'}, False),
        ({'place': 'village', 'name': 'Raisdorf'}, False),
        ({'name': 'Dorstfeld'}, False),
    ])
    def test_suburbs_and_quarters_with_a_name(self, importer, tags, expected):
        assert importer._is_node_placesubarea(1, tags, (7.4, 51.5)) is expected

    def test_such_a_node_is_kept_from_the_extract(self):
        importer = GermanyImporter.__new__(GermanyImporter)
        assert importer._is_node_to_cache(1, {'place': 'suburb', 'name': 'Dorstfeld'}, (7.4, 51.5))
        assert importer._is_node_to_cache(1, {'place': 'town', 'name': 'Singen'}, (8.8, 47.7))
        assert not importer._is_node_to_cache(1, {'place': 'neighbourhood', 'name': 'x'}, (8.8, 47.7))


class TestImport:
    @pytest.fixture
    def importer(self):
        instance = BaseImporter.__new__(BaseImporter)
        instance._within_country = lambda geometry: True
        instance.already_imported_places_node_ids = set()
        instance.written = []
        instance._upsert = lambda model, key, osm_id, defaults: instance.written.append(
            (model, key, osm_id, defaults))
        return instance

    def test_a_suburb_becomes_a_place_subarea_with_no_border(self, importer):
        importer._import_node(248915416, {'place': 'suburb', 'name': 'Dorstfeld', 'name:pl': 'Dorstfeld (pl)',
                                          'population': '15295'}, (7.41, 51.50))
        (model, key, osm_id, defaults), = importer.written
        assert (model, key, osm_id) == (PlaceSubarea, 'node_osm_id', 248915416)
        assert defaults == {'name': 'Dorstfeld', 'names': {'pl': 'Dorstfeld (pl)'},
                            'center': Point(7.41, 51.50), 'population': '15295'}

    def test_outside_the_country_it_is_left(self, importer):
        importer._within_country = lambda geometry: False
        importer._import_node(1, {'place': 'suburb', 'name': 'Elsewhere'}, (0, 0))
        assert importer.written == []


@pytest.mark.django_db
class TestMerge:
    def test_a_point_inside_a_border_of_its_name_is_that_district(self):
        border = PlaceSubarea.objects.create(name='Huckarde', border=MultiPolygon(square(7.40, 51.52, 0.05)))
        point = PlaceSubarea.objects.create(name='Huckarde', center=Point(7.42, 51.54), node_osm_id=11)
        assert BaseImporter._merge_district_points() == (1, 0)
        point.refresh_from_db()
        assert point.is_merged and point.merged_into_id == border.id
        border.refresh_from_db()
        assert not border.is_merged

    @pytest.mark.parametrize('border_name,point_name', [
        ('Ville-Haute', 'Ville Haute'), ('St. Pauli', 'St Pauli'), ('Alt-Radebeul', 'Alt Radebeul'),
    ])
    def test_hyphens_spaces_and_dots_aside(self, border_name, point_name):
        border = PlaceSubarea.objects.create(name=border_name, border=MultiPolygon(square(6.1, 49.6, 0.05)))
        point = PlaceSubarea.objects.create(name=point_name, center=Point(6.12, 49.62), node_osm_id=21)
        BaseImporter._merge_district_points()
        point.refresh_from_db()
        assert point.merged_into_id == border.id

    def test_the_smallest_border_of_the_name_takes_it(self):
        PlaceSubarea.objects.create(name='Mitte', border=MultiPolygon(square(7.0, 51.0, 1.0)))
        inner = PlaceSubarea.objects.create(name='Mitte', border=MultiPolygon(square(7.4, 51.4, 0.1)))
        point = PlaceSubarea.objects.create(name='mitte', center=Point(7.45, 51.45), node_osm_id=12)
        BaseImporter._merge_district_points()
        point.refresh_from_db()
        assert point.merged_into_id == inner.id

    def test_a_point_no_border_is_named_for_stays(self):
        """Bałuty the point and Łódź-Bałuty the border: both are typed."""
        PlaceSubarea.objects.create(name='Łódź-Bałuty', border=MultiPolygon(square(19.4, 51.78, 0.1)))
        point = PlaceSubarea.objects.create(name='Bałuty', center=Point(19.45, 51.80), node_osm_id=13)
        assert BaseImporter._merge_district_points() == (0, 0)
        point.refresh_from_db()
        assert not point.is_merged

    def test_a_point_outside_the_border_of_its_name_stays(self):
        PlaceSubarea.objects.create(name='Neustadt', border=MultiPolygon(square(7.0, 51.0, 0.1)))
        point = PlaceSubarea.objects.create(name='Neustadt', center=Point(9.0, 53.0), node_osm_id=14)
        BaseImporter._merge_district_points()
        point.refresh_from_db()
        assert not point.is_merged

    def test_a_point_named_as_the_place_around_it_adds_nothing(self):
        Place.objects.create(name='Gera', border=MultiPolygon(square(12.0, 50.8, 0.2)))
        point = PlaceSubarea.objects.create(name='Gera', center=Point(12.08, 50.88), node_osm_id=15)
        assert BaseImporter._merge_district_points() == (0, 1)
        point.refresh_from_db()
        assert point.is_merged and point.merged_into_id is None

    def test_points_are_not_joined_as_borders_are(self):
        """Two points of one name in a city have no border to add up."""
        city = Place.objects.create(name='Stadt', border=MultiPolygon(square(7.0, 51.0, 1.0)))
        PlaceSubarea.objects.create(name='Nord', center=Point(7.2, 51.8), node_osm_id=16)
        PlaceSubarea.objects.create(name='Nord', center=Point(7.8, 51.8), node_osm_id=17)
        assert list(BaseImporter._get_duplicated_placesubarea_names(city)) == []
        BaseImporter._merge_place_subareas(city)
        assert PlaceSubarea.objects.filter(name='Nord', is_merged=False).count() == 2

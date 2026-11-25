"""
Index mappings and document building.

No OpenSearch needed: a document is a pure function of a model instance.
"""
import pytest
from django.contrib.gis.geos import LineString, MultiLineString, MultiPolygon

from django_osm_locations.models import Place, Street
from django_osm_locations.search.indexes import INDEXES

from .conftest import square

pytestmark = pytest.mark.django_db


class TestMappings:
    def test_every_location_type_has_an_index(self):
        assert set(INDEXES) == {
            'countries', 'country-subareas', 'places', 'place-subareas', 'streets',
            'addresses',
        }

    def test_center_is_a_geo_point(self):
        assert INDEXES['places'].properties['center']['type'] == 'geo_point'

    def test_geometry_is_a_geo_shape(self):
        for name in ('places', 'streets'):
            assert INDEXES[name].properties['geometry']['type'] == 'geo_shape'

    def test_border_types_carry_area_and_population(self):
        assert 'area' in INDEXES['places'].properties
        assert 'population' in INDEXES['places'].properties

    @pytest.mark.parametrize('name', ['streets', 'place-subareas', 'addresses'])
    def test_streets_districts_and_addresses_carry_their_places_population(self, name):
        assert INDEXES[name].properties['place_population'] == {'type': 'long'}

    @pytest.mark.parametrize('name', ['countries', 'country-subareas', 'places'])
    def test_and_nothing_else_does(self, name):
        assert 'place_population' not in INDEXES[name].properties

    def test_streets_carry_length_not_area(self):
        assert 'length' in INDEXES['streets'].properties
        assert 'area' not in INDEXES['streets'].properties

    def test_name_is_prefix_analysed_but_queries_are_not(self):
        """n-gramming the query as well as the index would match far too much."""
        name = INDEXES['places'].properties['name']
        assert name['analyzer'] == 'location_prefix'
        assert name['search_analyzer'] == 'location_prefix_search'

    def test_alias_and_index_names(self):
        index = INDEXES['places']
        assert index.alias('locations') == 'locations-places'
        assert index.index_name('locations', '20260101') == 'locations-places-20260101'


class TestDocuments:
    def test_place_document(self, place):
        doc = INDEXES['places'].document(place)
        assert doc['id'] == place.id
        assert doc['type'] == 'places'
        assert doc['name'] == 'Kraków'
        assert doc['slug'] == 'krakow'
        assert doc['area'] > 0
        assert len(doc['bbox']) == 4

    def test_center_is_lon_lat(self, place):
        doc = INDEXES['places'].document(place)
        assert set(doc['center']) == {'lon', 'lat'}
        assert doc['center']['lon'] == place.center[0]

    def test_geometry_is_geojson(self, place):
        geometry = INDEXES['places'].document(place)['geometry']
        assert geometry['type'] == 'MultiPolygon'
        assert geometry['coordinates']

    def test_street_document_uses_lines(self, street):
        doc = INDEXES['streets'].document(street)
        assert doc['length'] > 0
        assert doc['geometry']['type'] == 'MultiLineString'

    def test_missing_geometry_is_none(self):
        place = Place.objects.create(name='No Border', center=MultiPolygon(square(4, 4)).point_on_surface)
        assert INDEXES['places'].document(place)['geometry'] is None

    def test_osm_ids_are_grouped(self):
        street = Street.objects.create(
            name='Way Street', way_osm_id=42,
            lines=MultiLineString(LineString((0, 0), (0.1, 0.1))),
        )
        assert INDEXES['streets'].document(street)['osm']['way_id'] == 42

    def test_ancestry_is_flattened_for_filtering(self):
        street = Street.objects.create(
            name='Floriańska',
            lines=MultiLineString(LineString((1, 1), (1.1, 1.1))),
            place=[['Kraków', 'krakow']],
            country=[['Polska', 'polska']],
        )
        doc = INDEXES['streets'].document(street)
        assert 'krakow' in doc['ancestry_slugs']
        assert 'Kraków' in doc['ancestry_names']
        assert {'level': 'place', 'slug': 'krakow', 'name': 'Kraków'} in doc['ancestry']

    def test_empty_ancestry(self, street):
        doc = INDEXES['streets'].document(street)
        assert doc['ancestry'] == []
        assert doc['ancestry_slugs'] == []


class TestQueryset:
    def test_merged_rows_are_left_out_of_the_index(self, place):
        """They stay in the database; they are just not searchable."""
        assert INDEXES['places'].queryset().count() == 1
        place.mark_as_merged()
        assert INDEXES['places'].queryset().count() == 0
        assert Place.objects.count() == 1, 'merging must not delete anything'

    def test_countries_have_no_merged_flag(self, country):
        """
        Country has no is_merged column, so the queryset is simply every row --
        including the ones migration 0031 seeds.
        """
        from django_osm_locations.models import Country
        assert INDEXES['countries'].queryset().count() == Country.objects.count()
        assert INDEXES['countries'].queryset().filter(pk=country.pk).exists()


class TestPlacePopulation:
    """The size a street or a district ranks by is its place's."""

    def street(self, place):
        return Street(id=1, name='Marszałkowska', slug='marszalkowska', place=place, bbox=[])

    def test_the_population_of_the_place_it_is_in(self):
        doc = INDEXES['streets'].document(
            self.street([['Warszawa', 'warszawa']]), populations={'warszawa': 1863845})
        assert doc['place_population'] == 1863845

    def test_the_largest_where_a_place_lies_within_another(self):
        doc = INDEXES['streets'].document(
            self.street([['Kraków', 'krakow'], ['Bieżanów', 'biezanow']]),
            populations={'krakow': 800000, 'biezanow': 300})
        assert doc['place_population'] == 800000

    @pytest.mark.parametrize('place,populations', [
        ([['Kowale', 'kowale']], {'kowale': None}),
        ([['Kowale', 'kowale']], {}),
        ([], {'kowale': 500}),
        (None, None),
    ])
    def test_none_without_one(self, place, populations):
        doc = INDEXES['streets'].document(self.street(place), populations=populations)
        assert doc['place_population'] is None

    def test_a_place_has_none_being_one(self, place):
        assert 'place_population' not in INDEXES['places'].document(place)

    @pytest.mark.django_db
    def test_looked_up_once_for_a_batch_and_not_again(self, django_assert_num_queries):
        Place.objects.create(name='Warszawa', slug='warszawa', population=1863845)
        Place.objects.create(name='Kowale', slug='kowale')
        batch = [self.street([['Warszawa', 'warszawa']]), self.street([['Kowale', 'kowale']])]
        with django_assert_num_queries(1):
            found = INDEXES['streets'].place_populations(batch)
        assert (found['warszawa'], found['kowale']) == (1863845, None)
        with django_assert_num_queries(0):
            assert INDEXES['streets'].place_populations(batch, found) is found

    def test_no_lookup_for_a_type_without_it(self, django_assert_num_queries):
        with django_assert_num_queries(0):
            assert INDEXES['places'].place_populations([]) == {}

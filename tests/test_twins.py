"""
A place twice: Deurne, a point of 86,297 people, and Deurne, the district of
Antwerpen around it.

Search found both, and the point first. The point stays -- streets name it
as their settlement -- and is marked as the district's twin, which search
leaves out; the district takes its population to rank by.
"""
import pytest
from django.contrib.gis.geos import MultiPolygon, Point

from django_osm_locations.models import Address, Place, PlaceSubarea
from django_osm_locations.search.indexes import INDEXES
from django_osm_locations.utils.twins import TWIN_OF, innermost_place, name_key, twins_of

from .conftest import square

pytestmark = pytest.mark.django_db

ANTWERPEN = ['Antwerpen', 'antwerpen']


@pytest.fixture
def deurne():
    district = PlaceSubarea.objects.create(
        name='Deurne', slug='deurne', place=[ANTWERPEN],
        border=MultiPolygon(square(4.45, 51.2, 0.03)))
    point = Place.objects.create(name='Deurne', slug='deurne-node', place=[ANTWERPEN],
                                 center=Point(4.46, 51.21), population=86297)
    return point, district


def documents(point, district):
    places, districts = INDEXES['places'], INDEXES['place-subareas']
    return (places.document(point, twin=places.twin_fields([point.id]).get(point.id)),
            districts.document(district, twin=districts.twin_fields([district.id]).get(district.id)))


class TestTheTwins:
    def test_the_point_is_the_districts_twin(self, deurne):
        point, district = documents(*deurne)
        assert point[TWIN_OF] == 'deurne'

    def test_the_district_ranks_by_its_twins_people(self, deurne):
        point, district = documents(*deurne)
        assert district['population'] == 86297

    def test_unless_it_has_its_own(self, deurne):
        _, district = deurne
        district.population = 80000
        district.save()
        assert documents(*deurne)[1]['population'] == 80000

    def test_case_aside(self, deurne):
        point, _ = deurne
        point.name = 'DEURNE'
        point.save()
        assert documents(*deurne)[0][TWIN_OF] == 'deurne'

    def test_not_a_point_outside_the_district(self, deurne):
        point, _ = deurne
        point.center = Point(4.6, 51.3)
        point.save()
        assert documents(*deurne)[0][TWIN_OF] is None

    def test_not_one_in_another_city(self, deurne):
        point, _ = deurne
        point.place = [['Mortsel', 'mortsel']]
        point.save()
        assert documents(*deurne)[0][TWIN_OF] is None

    def test_not_a_place_with_a_border_of_its_own(self, deurne):
        point, _ = deurne
        point.border = MultiPolygon(square(4.455, 51.205, 0.01))
        point.save()
        assert twins_of('place', [point.id]) == {}

    def test_another_name_is_no_twin(self, deurne):
        point, _ = deurne
        point.name = 'Borgerhout'
        point.save()
        assert documents(*deurne)[0][TWIN_OF] is None


class TestTheRule:
    def test_the_innermost_place(self):
        entries = [{'level': 'place', 'slug': 'antwerpen'}, {'level': 'place', 'slug': 'deurne'}]
        assert innermost_place(entries) == 'deurne'

    def test_case_aside(self):
        assert name_key('DEURNE') == name_key('Deurne')


class TestAHouseThatSharedASlug:
    def test_the_slug_it_shared_is_in_the_index(self):
        house = Address.objects.create(name='Østergade 12', house_number='12',
                                       street=[['Østergade', 'ostergade']],
                                       slug='ostergade-12-rudkobing', center=Point(10.7, 54.9))
        assert INDEXES['addresses'].document(house)['shared_slug'] == 'ostergade-12'

    def test_one_that_did_not_has_none(self):
        house = Address.objects.create(name='Østergade 12', house_number='12',
                                       street=[['Østergade', 'ostergade']],
                                       slug='ostergade-12', center=Point(10.7, 54.9))
        assert INDEXES['addresses'].document(house)['shared_slug'] is None

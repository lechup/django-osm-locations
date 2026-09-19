"""
admin_level is kept, not discarded.

Which model a boundary becomes depends on it, and it used to be thrown away
afterwards. With one country in the database that was survivable; with several
it means a constituent country, an English county and a Polish gmina all sit in
country-subareas with nothing telling them apart.
"""
import pytest
from django.contrib.gis.geos import MultiPolygon

from django_osm_locations.api.queries import QueryError, build_body, parse_admin_level
from django_osm_locations.management.commands.importers.base import BaseImporter
from django_osm_locations.models import CountrySubarea, Place, Street
from django_osm_locations.search.indexes import INDEXES

from .conftest import square


class TestParsingTheTag:
    @pytest.mark.parametrize('tags,expected', [
        ({'admin_level': '4'}, 4),
        ({'admin_level': '10'}, 10),
        ({}, None),
        ({'admin_level': 'not a number'}, None),
        ({'admin_level': None}, None),
    ])
    def test_admin_level_from_tags(self, tags, expected):
        assert BaseImporter._admin_level(tags) == expected


@pytest.mark.django_db
class TestStoredOnTheRow:
    def test_a_division_keeps_its_level(self):
        region = CountrySubarea.objects.create(
            name='Region Midtjylland', border=MultiPolygon(square(9, 56)), admin_level=4,
        )
        region.refresh_from_db()
        assert region.admin_level == 4

    def test_levels_tell_apart_what_used_to_be_flat(self):
        """A constituent country, a county and a gmina, in one table."""
        CountrySubarea.objects.create(name='England', border=MultiPolygon(square(0, 51)), admin_level=4)
        CountrySubarea.objects.create(name='Herefordshire', border=MultiPolygon(square(2, 52)), admin_level=6)
        CountrySubarea.objects.create(name='gmina Zakopane', border=MultiPolygon(square(19, 49)), admin_level=7)

        assert CountrySubarea.objects.filter(admin_level=4).count() == 1
        assert CountrySubarea.objects.filter(admin_level__lte=5).count() == 1
        assert list(
            CountrySubarea.objects.order_by('admin_level').values_list('name', flat=True)
        ) == ['England', 'Herefordshire', 'gmina Zakopane']

    def test_null_for_anything_not_from_a_boundary(self):
        """A place node or a street has no admin level."""
        from django.contrib.gis.geos import LineString, MultiLineString

        street = Street.objects.create(
            name='Abbey Road', lines=MultiLineString(LineString((0, 0), (0.1, 0.1))),
        )
        assert street.admin_level is None
        assert Place.objects.create(name='Somewhere').admin_level is None


@pytest.mark.django_db
class TestInTheIndex:
    def test_the_document_carries_it(self):
        region = CountrySubarea.objects.create(
            name='England', border=MultiPolygon(square(0, 51)), admin_level=4,
        )
        assert INDEXES['country-subareas'].document(region)['admin_level'] == 4

    def test_it_is_mapped(self):
        assert INDEXES['country-subareas'].properties['admin_level']['type'] == 'short'

    def test_null_survives_into_the_document(self, street):
        assert INDEXES['streets'].document(street)['admin_level'] is None


class TestFiltering:
    def test_a_single_level(self):
        assert parse_admin_level('4') == {'terms': {'admin_level': [4]}}

    def test_several(self):
        assert parse_admin_level('4,6') == {'terms': {'admin_level': [4, 6]}}

    def test_absent(self):
        assert parse_admin_level(None) is None

    def test_rejects_junk(self):
        with pytest.raises(QueryError):
            parse_admin_level('four')

    def test_reaches_the_query_body(self):
        body, _, _ = build_body({'admin_level': '4'})
        assert {'terms': {'admin_level': [4]}} in body['query']['bool']['filter']

    def test_it_filters_rather_than_scores(self):
        """Nothing about a level says a location matches the text better."""
        body, _, _ = build_body({'q': 'england', 'admin_level': '4'})
        scored = body['query']['function_score']['query']['bool']
        assert {'terms': {'admin_level': [4]}} in scored['filter']
        assert 'admin_level' not in str(scored.get('must', ''))

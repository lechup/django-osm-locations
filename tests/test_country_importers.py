"""
Per-country admin_level conventions, and keeping the neighbours out.

The levels below were read from each country's own Geofabrik extract, not from
the wiki -- Isle of Man taught that lesson. An extract is a rectangle, so every
one of these also contains slices of its neighbours.
"""
import pytest

from django_osm_locations.management.commands.importers.denmark import DenmarkImporter
from django_osm_locations.management.commands.importers.finland import FinlandImporter
from django_osm_locations.management.commands.importers.netherlands import NetherlandsImporter
from django_osm_locations.management.commands.importers.norway import NorwayImporter
from django_osm_locations.management.commands.importers.nordic import AdminLevelImporter

ADMIN = {'boundary': 'administrative', 'name': 'Somewhere'}


def admin(level, **extra):
    return {**ADMIN, 'admin_level': level, **extra}


class TestLevelsPerCountry:
    @pytest.mark.parametrize('importer,subareas,places,subplaces', [
        # Denmark: 5 regions, 98 kommuner, no admin level for towns
        (DenmarkImporter, ('4',), ('7',), ('9', '10')),
        # Norway: fylker, 389 kommuner, bydeler in Oslo and Bergen
        (NorwayImporter, ('4',), ('7',), ('9',)),
        # Finland: maakunnat and seutukunnat, municipalities one level deeper
        (FinlandImporter, ('4', '7'), ('8',), ('9', '10', '11')),
        # Netherlands: 12 provinces, 432 gemeenten, wijken and buurten
        (NetherlandsImporter, ('4',), ('8',), ('9', '10')),
    ])
    def test_levels(self, importer, subareas, places, subplaces):
        assert importer.countrysubarea_levels == subareas
        assert importer.place_levels == places
        assert importer.placesubarea_levels == subplaces

    @pytest.mark.parametrize('importer', [
        DenmarkImporter, NorwayImporter, FinlandImporter, NetherlandsImporter,
    ])
    def test_a_level_is_never_two_things_at_once(self, importer):
        levels = (importer.countrysubarea_levels + importer.place_levels
                  + importer.placesubarea_levels)
        assert len(levels) == len(set(levels)), importer.__name__


class TestPredicates:
    def test_denmark_kommune_is_a_place_not_a_subarea(self):
        kommune = admin('7')
        assert DenmarkImporter._is_relation_place(1, kommune, []) is True
        assert DenmarkImporter._is_relation_countrysubarea(1, kommune, []) is False

    def test_finland_municipality_is_one_level_deeper_than_denmark(self):
        assert FinlandImporter._is_relation_place(1, admin('8'), []) is True
        assert FinlandImporter._is_relation_place(1, admin('7'), []) is False
        assert DenmarkImporter._is_relation_place(1, admin('8'), []) is False

    def test_finland_treats_seutukunta_as_a_division(self):
        assert FinlandImporter._is_relation_countrysubarea(1, admin('7'), []) is True

    def test_a_boundary_must_be_administrative(self):
        for tags in ({'admin_level': '4', 'name': 'X'},
                     {'admin_level': '4', 'name': 'X', 'boundary': 'political'}):
            assert DenmarkImporter._is_relation_countrysubarea(1, tags, []) is False

    def test_a_boundary_must_be_named(self):
        assert DenmarkImporter._is_relation_countrysubarea(
            1, {'admin_level': '4', 'boundary': 'administrative'}, []) is False

    @pytest.mark.parametrize('place_type,expected', [
        ('city', True), ('town', True), ('village', True), ('hamlet', True),
        # Norway tags 105,989 farms and 30,469 isolated dwellings as places
        ('farm', False), ('isolated_dwelling', False), ('locality', False),
        ('islet', False),
    ])
    def test_only_real_settlements_become_places(self, place_type, expected):
        tags = {'place': place_type, 'name': 'X'}
        assert NorwayImporter._is_node_place(1, tags, (0, 0)) is expected


class TestCountryBoundary:
    """
    The country's own admin_level=2 relation is what keeps the neighbours out.
    """

    class FakeCountry:
        iso2 = 'DK'

    @pytest.fixture
    def importer(self):
        instance = DenmarkImporter.__new__(DenmarkImporter)
        instance.country = self.FakeCountry()
        instance.country_boundary = None
        return instance

    def test_matches_on_the_iso_code(self, importer):
        assert importer._is_country_boundary(
            {'admin_level': '2', 'ISO3166-1': 'DK', 'name': 'Danmark'}) is True

    def test_rejects_a_neighbour(self, importer):
        assert importer._is_country_boundary(
            {'admin_level': '2', 'ISO3166-1': 'SE', 'name': 'Sverige'}) is False

    def test_ignores_lower_levels(self, importer):
        assert importer._is_country_boundary(
            {'admin_level': '4', 'ISO3166-1': 'DK', 'name': 'X'}) is False

    def test_a_name_alone_is_not_enough(self, importer):
        """
        Names in an extract are local and can carry several languages at once --
        "Suomi / Finland", "België / Belgique / Belgien" -- so the code decides.
        """
        assert importer._is_country_boundary(
            {'admin_level': '2', 'name': 'Danmark'}) is False

    def test_accepts_everything_when_no_boundary_was_found(self, importer):
        """
        A missing or broken level-2 relation degrades to the old behaviour
        rather than importing nothing at all.
        """
        from django.contrib.gis.geos import Point
        assert importer._within_country(Point(0, 0)) is True
        assert importer._within_country(None) is True


class TestSharedBase:
    def test_the_four_share_one_implementation(self):
        for importer in (DenmarkImporter, NorwayImporter, FinlandImporter,
                         NetherlandsImporter):
            assert issubclass(importer, AdminLevelImporter)

    def test_the_base_matches_nothing_on_its_own(self):
        """Levels are empty until a country declares them."""
        assert AdminLevelImporter._is_relation_place(1, admin('8'), []) is False

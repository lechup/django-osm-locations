"""
Per-country admin_level conventions, and keeping the neighbours out.

The levels below were read from each country's own Geofabrik extract, not from
the wiki -- Isle of Man taught that lesson. An extract is a rectangle, so every
one of these also contains slices of its neighbours.
"""
import pytest

from django_osm_locations.management.commands.importers.denmark import DenmarkImporter
from django_osm_locations.management.commands.importers.france import FranceImporter
from django_osm_locations.management.commands.importers.finland import FinlandImporter
from django_osm_locations.management.commands.importers.netherlands import NetherlandsImporter
from django_osm_locations.management.commands.importers.norway import NorwayImporter
from django_osm_locations.management.commands.importers.united_kingdom import UnitedKingdomImporter
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
        # UK: nations, metropolitan counties, counties and Northern
        # Ireland's districts are all divisions; districts elsewhere are 8
        (UnitedKingdomImporter, ('4', '5', '6', '7'), ('8',), ('9', '10')),
        # France: regions and departements, then 35,099 communes
        (FranceImporter, ('4', '6'), ('8',), ('9', '10', '11')),
    ])
    def test_levels(self, importer, subareas, places, subplaces):
        assert importer.countrysubarea_levels == subareas
        assert importer.place_levels == places
        assert importer.placesubarea_levels == subplaces

    @pytest.mark.parametrize('importer', [
        DenmarkImporter, NorwayImporter, FinlandImporter, NetherlandsImporter,
        UnitedKingdomImporter, FranceImporter,
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


class TestRegistry:
    """
    Adding a country is one file. The class says which extract it reads and
    which country it is, and everything else follows from that -- no download
    table, no slug table, no migration.
    """

    def test_every_country_is_discovered(self):
        from django_osm_locations.management.commands.importers import known_countries

        assert known_countries() == [
            'denmark', 'finland', 'france', 'germany', 'isle-of-man',
            'netherlands', 'norway', 'poland', 'united-kingdom',
        ]

    def test_shared_machinery_is_not_mistaken_for_a_country(self):
        from django_osm_locations.management.commands.importers import registry

        assert 'base' not in registry()
        assert 'nordic' not in registry()

    def test_every_importer_declares_what_it_needs(self):
        from django_osm_locations.management.commands.importers import registry

        for name, importer in registry().items():
            for attribute in ('country_name', 'country_slug', 'iso2', 'iso3', 'un'):
                assert getattr(importer, attribute), '{} has no {}'.format(name, attribute)

    def test_identifiers_are_unique(self):
        from django_osm_locations.management.commands.importers import registry

        importers = list(registry().values())
        for attribute in ('country_slug', 'iso2', 'iso3', 'un'):
            values = [getattr(i, attribute) for i in importers]
            assert len(values) == len(set(values)), attribute

    def test_the_download_url_comes_from_the_class(self):
        from django_osm_locations.management.commands.locations_import import Command

        url = Command()._get_osm_url('denmark')
        assert url.endswith('europe/denmark-latest.osm.pbf')

    def test_an_unknown_name_is_simply_absent(self):
        from django_osm_locations.management.commands.importers import get_importer

        assert get_importer('atlantis') is None


class TestUnitedKingdom:
    """
    The UK is admin_level 2 with ISO3166-1 GB, while England, Scotland, Wales
    and Northern Ireland are admin_level 4 with subdivision codes -- countries
    constitutionally, subdivisions in the data.
    """

    def test_the_four_nations_are_divisions_not_countries(self):
        england = admin('4', **{'ISO3166-2': 'GB-ENG'})
        assert UnitedKingdomImporter._is_relation_countrysubarea(1, england, []) is True
        assert UnitedKingdomImporter._is_relation_place(1, england, []) is False

    def test_three_tiers_of_division(self):
        """Nations at 4, metropolitan counties at 5, counties at 6."""
        for level in ('4', '5', '6'):
            assert UnitedKingdomImporter._is_relation_countrysubarea(1, admin(level), []) is True

    def test_northern_irish_districts_are_divisions_not_places(self):
        """
        Level 7 is Northern Ireland's eleven districts and nothing else, and a
        district is a division in the same sense as an English county.

        They were places until Belfast showed what that cost: Belfast City
        District, a boundary with no population, stood beside Belfast, a node
        with 333,000 people and no border, and no merge rule could join them
        because the names differ. Northern Irish settlements come from
        ``place=*`` nodes regardless, so nothing is lost by moving 7.
        """
        assert UnitedKingdomImporter._is_relation_countrysubarea(1, admin('7'), []) is True
        assert UnitedKingdomImporter._is_relation_place(1, admin('7'), []) is False

    def test_districts_elsewhere_in_the_uk_are_places(self):
        assert UnitedKingdomImporter._is_relation_place(1, admin('8'), []) is True

    def test_civil_parishes_are_below_a_place(self):
        assert UnitedKingdomImporter._is_relation_placesubarea(1, admin('10'), []) is True


class TestFrance:
    def test_regions_and_departements_are_divisions(self):
        for level in ('4', '6'):
            assert FranceImporter._is_relation_countrysubarea(1, admin(level), []) is True

    def test_communes_are_places(self):
        assert FranceImporter._is_relation_place(1, admin('8'), []) is True

    def test_german_tiers_along_the_rhine_are_left_out(self):
        """
        Levels 5 and 7 in the France extract are Regierungsbezirke and
        Verbandsgemeinden, not anything French.
        """
        for level in ('5', '7'):
            assert FranceImporter._is_relation_countrysubarea(1, admin(level), []) is False
            assert FranceImporter._is_relation_place(1, admin(level), []) is False


@pytest.mark.django_db
class TestUnitedKingdomAfterImport:
    """
    Level 7 moved from places to divisions, and nothing is deleted on import.

    A relation that was a place under the old levels and is a country subarea
    under the new ones therefore has a row in both tables. The subarea is the
    live one.
    """

    @pytest.fixture
    def importer(self):
        from unittest import mock

        from django_osm_locations.models import Country

        instance = UnitedKingdomImporter.__new__(UnitedKingdomImporter)
        instance.country = Country.objects.create(
            name='United Kingdom', slug='united-kingdom', iso2='GB', iso3='GBR', un=826,
        )
        instance.show_progress = False
        return instance

    def test_a_place_left_over_from_the_old_levels_is_retired(self, importer):
        from django_osm_locations.models import CountrySubarea, Place

        CountrySubarea.objects.create(name='Belfast City District', relation_osm_id=19414244)
        stale = Place.objects.create(name='Belfast City District', relation_osm_id=19414244)

        importer.after_import()

        stale.refresh_from_db()
        assert stale.is_merged is True

    def test_the_settlement_itself_is_untouched(self, importer):
        """Belfast the node stays: it is the place people search for."""
        from django_osm_locations.models import CountrySubarea, Place

        CountrySubarea.objects.create(name='Belfast City District', relation_osm_id=19414244)
        belfast = Place.objects.create(name='Belfast', node_osm_id=1418701024, population=333000)

        importer.after_import()

        belfast.refresh_from_db()
        assert belfast.is_merged is False

    def test_places_sharing_no_relation_are_untouched(self, importer):
        from django_osm_locations.models import CountrySubarea, Place

        CountrySubarea.objects.create(name='Belfast City District', relation_osm_id=19414244)
        other = Place.objects.create(name='Lisburn', relation_osm_id=999)

        importer.after_import()

        other.refresh_from_db()
        assert other.is_merged is False

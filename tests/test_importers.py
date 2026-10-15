"""
Country importer predicates.

Each country subclasses BaseImporter and decides, from OSM tags, what counts as
a country subarea / place / place subarea. These are pure classmethods over
tag dicts, so they need no database and no .pbf file.
"""
import pytest

from django_osm_locations.management.commands.importers.germany import GermanyImporter
from django_osm_locations.management.commands.importers.isle_of_man import IsleOfManImporter
from django_osm_locations.management.commands.importers.poland import PolandImporter

ADMIN = {'boundary': 'administrative', 'name': 'X'}


def admin(level, **extra):
    return {**ADMIN, 'admin_level': level, **extra}


@pytest.mark.parametrize('level,expected', [
    ('4', True), ('6', True), ('7', True),   # województwo / powiat
    ('8', False),                            # that is a Place
    ('9', False), ('10', False),
])
def test_poland_country_subarea_levels(level, expected):
    assert PolandImporter._is_relation_countrysubarea(1, admin(level), []) is expected


@pytest.mark.parametrize('level,expected', [('8', True), ('7', False), ('9', False)])
def test_poland_place_levels(level, expected):
    assert PolandImporter._is_relation_place(1, admin(level), []) is expected


@pytest.mark.parametrize('level,expected', [('9', True), ('10', True), ('11', True), ('8', False)])
def test_poland_place_subarea_levels(level, expected):
    assert PolandImporter._is_relation_placesubarea(1, admin(level), []) is expected


def test_poland_requires_administrative_boundary():
    assert PolandImporter._is_relation_countrysubarea(1, {'admin_level': '4', 'name': 'X'}, []) is False
    assert PolandImporter._is_relation_countrysubarea(
        1, {'admin_level': '4', 'name': 'X', 'boundary': 'political'}, []
    ) is False


def test_poland_requires_name():
    assert PolandImporter._is_relation_countrysubarea(
        1, {'admin_level': '4', 'boundary': 'administrative'}, []
    ) is False


@pytest.mark.parametrize('place_type', ['city', 'village', 'hamlet', 'town'])
def test_place_node_types(place_type):
    assert PolandImporter._is_node_place(1, {'place': place_type, 'name': 'X'}, (0, 0)) is True


def test_place_node_rejects_other_types():
    assert PolandImporter._is_node_place(1, {'place': 'suburb', 'name': 'X'}, (0, 0)) is False


def test_way_place_needs_a_closed_way():
    tags = {'place': 'city', 'name': 'X'}
    assert PolandImporter._is_way_place(1, tags, [1, 2, 3, 1]) is True   # closed
    assert PolandImporter._is_way_place(1, tags, [1, 2, 3, 4]) is False  # open
    assert PolandImporter._is_way_place(1, tags, [1, 2]) is False        # too few


def test_germany_city_relations_are_places_not_subareas():
    """
    Germany marks some cities at country-subarea admin levels and distinguishes
    them with de:place=city -- see openstreetmap.org/relation/62649.
    """
    city = admin('6', **{'de:place': 'city'})
    assert GermanyImporter._is_relation_place(1, city, []) is True
    assert GermanyImporter._is_relation_countrysubarea(1, city, []) is False

    region = admin('6', **{'de:place': 'region'})
    assert GermanyImporter._is_relation_countrysubarea(1, region, []) is True


def test_germany_a_region_without_de_place_is_still_a_region():
    """Most Länder and Kreise carry no de:place; they were all being dropped."""
    for level in ('4', '5', '6', '7'):
        assert GermanyImporter._is_relation_countrysubarea(1, admin(level), []) is True
        assert GermanyImporter._is_relation_place(1, admin(level), []) is False


def test_germany_whitelists_its_own_tag():
    assert 'de:place' in GermanyImporter.whitelist_tags
    # the base tags must survive subclassing
    assert 'admin_level' in GermanyImporter.whitelist_tags


def test_isle_of_man_importer_imports():
    """It used a Python 2 implicit relative import and would not load at all."""
    assert issubclass(IsleOfManImporter, IsleOfManImporter.__mro__[1])

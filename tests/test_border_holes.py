"""
A relation's inner ways are holes in it.

Every member went into one polygonize, and a ring with a hole came out as the
ring and the hole as two faces, whose union filled the hole: Niederösterreich
covered Vienna, Bezirk Graz-Umgebung covered Graz, Italy covered San Marino.
"""
from array import array

from django.contrib.gis.geos import Point

from django_osm_locations.management.commands.importers.nordic import AdminLevelImporter


def ring(x0, y0, x1, y1):
    return array('d', [x0, y0, x1, y0, x1, y1, x0, y1, x0, y0])


class Importer(AdminLevelImporter):
    def __init__(self, ways):
        self.ways = ways


LOWER_AUSTRIA = {1: ({}, ring(0, 0, 10, 10)), 2: ({}, ring(4, 4, 6, 6))}


def test_a_city_inside_its_land_is_not_part_of_it():
    border = Importer(LOWER_AUSTRIA)._get_border_from_members([(1, 'w', 'outer'), (2, 'w', 'inner')])
    assert not border.contains(Point(5, 5)), 'Vienna is a hole in Lower Austria'
    assert border.contains(Point(1, 1))
    assert abs(border.area - 96) < 1e-9


def test_without_an_inner_way_nothing_changes():
    border = Importer(LOWER_AUSTRIA)._get_border_from_members([(1, 'w', 'outer')])
    assert border.contains(Point(5, 5))
    assert abs(border.area - 100) < 1e-9


def test_a_member_without_a_role_is_outer():
    border = Importer(LOWER_AUSTRIA)._get_border_from_members([(1, 'w', ''), (2, 'w', 'inner')])
    assert not border.contains(Point(5, 5))


def test_an_outer_ring_made_of_several_ways():
    ways = {1: ({}, array('d', [0, 0, 10, 0, 10, 10])), 3: ({}, array('d', [10, 10, 0, 10, 0, 0])),
            2: ({}, ring(4, 4, 6, 6))}
    border = Importer(ways)._get_border_from_members(
        [(1, 'w', 'outer'), (3, 'w', 'outer'), (2, 'w', 'inner')])
    assert not border.contains(Point(5, 5))
    assert border.geom_type == 'MultiPolygon'


def test_a_border_missing_a_way_has_its_open_ends_found():
    """Alaska's said "assembled" with its mainland's ring open, and imported nearly empty."""
    ways = {1: ({}, array('d', [0, 0, 10, 0, 10, 10])), 3: ({}, array('d', [10, 10, 0, 10]))}
    members = [(1, 'w', 'outer'), (3, 'w', 'outer'), (4, 'w', 'outer')]  # 4 is not in the extract
    assert Importer(ways)._open_ends(members) == [(0.0, 0.0), (0.0, 10.0)]


def test_a_closed_border_has_none():
    assert Importer(LOWER_AUSTRIA)._open_ends([(1, 'w', 'outer'), (2, 'w', 'inner')]) == []

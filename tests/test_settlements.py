"""
One name in one place, and the streets it is.

The cases are the Denmark pack's: Østergade in Bagenkop and in Rudkøbing,
45 km apart and one street of Langeland, and a road running on from one
village into the next.
"""
from shapely.geometry import LineString

from django_osm_locations.utils.settlements import nearest, settlement_key, streets

# a metre east, near 55 degrees north
EAST = 1 / (111320 * 0.5736)


def line(start, metres):
    return LineString([(10 + start * EAST, 55), (10 + (start + metres) * EAST, 55)])


def house(at, settlement):
    return (10 + at * EAST, 55, settlement)


class TestOneStreet:
    def test_one_settlement_is_one_street(self):
        lines = [line(0, 300), line(5000, 300)]
        assert streets(lines, [house(100, 'Rudkøbing'), house(5100, 'Rudkøbing')]) == [[0, 1]]

    def test_no_houses_is_one_street(self):
        """A gap in the line is still the same street."""
        assert streets([line(0, 300), line(5000, 300)], []) == [[0, 1]]

    def test_pieces_that_touch_are_one_whatever_their_houses_say(self):
        """A road running on from one village into the next."""
        lines = [line(0, 300), line(320, 300)]
        assert streets(lines, [house(100, 'Rudkøbing'), house(500, 'Tranekær')]) == [[0, 1]]

    def test_case_is_no_other_settlement(self):
        lines = [line(0, 300), line(5000, 300)]
        assert streets(lines, [house(100, 'Sande MCN'), house(5100, 'Sande Mcn')]) == [[0, 1]]


class TestSeveral:
    def test_two_villages_are_two_streets(self):
        """Østergade in Rudkøbing and in Bagenkop."""
        lines = [line(0, 300), line(45000, 500)]
        found = streets(lines, [house(100, 'Rudkøbing'), house(45100, 'Bagenkop')])
        assert sorted(found) == [[0], [1]]

    def test_the_longest_first(self):
        lines = [line(0, 300), line(45000, 500)]
        found = streets(lines, [house(100, 'Rudkøbing'), house(45100, 'Bagenkop')])
        assert found[0] == [1]

    def test_a_piece_with_no_houses_goes_with_the_nearest_named(self):
        lines = [line(0, 300), line(45000, 500), line(45800, 100)]
        found = streets(lines, [house(100, 'Rudkøbing'), house(45100, 'Bagenkop')])
        assert sorted(found) == [[0], [1, 2]]

    def test_the_settlement_most_of_a_pieces_houses_name(self):
        lines = [line(0, 300), line(45000, 500)]
        houses = [house(100, 'Rudkøbing'), house(200, 'Rudkøbing'), house(150, 'Bagenkop'),
                  house(45100, 'Bagenkop')]
        assert sorted(streets(lines, houses)) == [[0], [1]]

    def test_two_pieces_of_one_village_far_apart_are_one(self):
        lines = [line(0, 300), line(3000, 300), line(45000, 500)]
        houses = [house(100, 'Rudkøbing'), house(3100, 'Rudkøbing'), house(45100, 'Bagenkop')]
        assert sorted(streets(lines, houses)) == [[0, 1], [2]]


class TestHousesOnThem:
    def test_each_by_the_street_nearest_it(self):
        lines = [line(0, 300), line(45000, 500)]
        groups = [[0], [1]]
        points = [house(100, '')[:2], house(45600, '')[:2]]
        assert nearest(lines, groups, points) == [0, 1]


def test_a_settlement_is_counted_case_aside():
    assert settlement_key(' Rudkøbing ') == settlement_key('rudkøbing')

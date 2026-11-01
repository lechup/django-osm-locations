"""
When two rows are one house, and where the one house is.

The cases are the Kraków and Łódź ones: Pawia 23 a building and two offices
in it, Lublańska 38 two buildings A1 and A2, Okólna 76 in Łódź and in
Łagiewniki Nowe six kilometres apart.
"""
import pytest

from django_osm_locations.utils.duplicates import (
    SAME_HOUSE_METRES,
    clusters,
    keeper,
    merged_center,
    merged_postcode,
    metres_between,
)

PAWIA_23 = (19.9450, 50.0700)


def east(point, metres):
    """``metres`` east of ``point``, at Kraków's latitude."""
    return (point[0] + metres / (111320 * 0.6428), point[1])


class TestDistance:
    def test_a_hundred_metres_east(self):
        assert metres_between(PAWIA_23, east(PAWIA_23, 100)) == pytest.approx(100, abs=0.5)

    def test_a_hundred_metres_north(self):
        north = (PAWIA_23[0], PAWIA_23[1] + 100 / 110540)
        assert metres_between(PAWIA_23, north) == pytest.approx(100, abs=0.5)


class TestClusters:
    def test_a_building_and_the_offices_in_it_are_one(self):
        points = [PAWIA_23, east(PAWIA_23, 8), east(PAWIA_23, 15)]
        assert clusters(points) == [[0, 1, 2]]

    def test_two_houses_far_apart_are_two(self):
        """Okólna 76 in Łódź and in Łagiewniki Nowe."""
        assert clusters([PAWIA_23, east(PAWIA_23, 6000)]) == [[0], [1]]

    def test_a_row_of_buildings_is_one_by_its_links(self):
        """A1 to A2 60 m, A2 to A3 60 m: A1 and A3 are 120 m apart and one house."""
        points = [PAWIA_23, east(PAWIA_23, 60), east(PAWIA_23, 120)]
        assert clusters(points) == [[0, 1, 2]]

    def test_the_limit_itself_is_in(self):
        inside = east(PAWIA_23, SAME_HOUSE_METRES - 0.5)
        outside = east(PAWIA_23, SAME_HOUSE_METRES + 0.5)
        assert clusters([PAWIA_23, inside]) == [[0, 1]]
        assert clusters([PAWIA_23, outside]) == [[0], [1]]

    def test_two_pairs(self):
        far = east(PAWIA_23, 5000)
        points = [PAWIA_23, far, east(PAWIA_23, 10), east(far, 10)]
        assert clusters(points) == [[0, 2], [1, 3]]

    def test_one_alone(self):
        assert clusters([PAWIA_23]) == [[0]]


class TestWhereTheHouseIs:
    def test_the_outline_and_not_the_shop_at_its_door(self):
        building, shop = PAWIA_23, east(PAWIA_23, 12)
        assert merged_center([shop, building], [False, True]) == building

    def test_two_buildings_between_them(self):
        """Lublańska 38, A1 and A2."""
        a2 = east(PAWIA_23, 40)
        assert merged_center([PAWIA_23, a2], [True, True]) == pytest.approx(east(PAWIA_23, 20))

    def test_points_alone_between_them(self):
        other = east(PAWIA_23, 18)
        assert merged_center([PAWIA_23, other], [False, False]) == pytest.approx(east(PAWIA_23, 9))


class TestWhichRowStays:
    def test_the_first_outline(self):
        assert keeper([False, True, True]) == 1

    def test_the_first_row_without_one(self):
        assert keeper([False, False]) == 0


class TestPostcode:
    def test_the_one_most_carry(self):
        assert merged_postcode(['31-154', '31-510', '31-510']) == '31-510'

    def test_one_missing_is_not_a_vote(self):
        assert merged_postcode(['', '31-154']) == '31-154'

    def test_the_first_on_a_tie(self):
        assert merged_postcode(['31-154', '31-510']) == '31-154'

    def test_none_at_all(self):
        assert merged_postcode(['', '']) == ''

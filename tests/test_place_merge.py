"""
Which places of one name the import folds into one.

Only a place with a border takes another, and only one inside that border:
a place mapped twice. A point of the same name nearby is another place --
Poland alone has 21,000 such pairs within 25 km of each other, and 8 of them
share a TERYT locality code.
"""
import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon

from django_osm_locations.management.commands.importers.base import BaseImporter
from django_osm_locations.models import Place

from .conftest import square

pytestmark = pytest.mark.django_db


def fresh(row):
    return Place.objects.get(pk=row.pk)


def town(name='Poręba', border=None):
    return Place.objects.create(name=name, border=MultiPolygon(border or square(19.30, 50.45, 0.08)),
                                relation_osm_id=2838653, population=8288)


def point(name='Poręba', x=19.34, y=50.49, osm_id=3009741545):
    return Place.objects.create(name=name, center=Point(x, y), node_osm_id=osm_id)


class TestATownAndAHamletOfItsName:
    def test_the_hamlet_does_not_take_the_town(self):
        """Poręba, 8,000 people, went into a hamlet of its name 14 km away, border and all."""
        stays = town()
        hamlet = point(y=50.62)

        BaseImporter._merge_places()

        assert fresh(stays).is_merged is False
        assert fresh(stays).border is not None
        assert fresh(hamlet).is_merged is False

    def test_a_point_inside_the_border_is_the_town_mapped_twice(self):
        stays = town()
        twice = point()

        BaseImporter._merge_places()

        assert fresh(twice).merged_into_id == stays.pk
        assert fresh(stays).is_merged is False

    def test_a_point_outside_the_border_is_another_place_even_inside_its_box(self):
        """The village of Lipno is 216 m outside the town of Lipno, and inside its bounding box."""
        ell = Polygon(((19.0, 52.0), (19.2, 52.0), (19.2, 52.1), (19.1, 52.1),
                       (19.1, 52.2), (19.0, 52.2), (19.0, 52.0)))
        stays = town(name='Lipno', border=ell)
        village = point(name='Lipno', x=19.15, y=52.15)

        BaseImporter._merge_places()

        assert fresh(village).is_merged is False
        assert fresh(stays).is_merged is False


class TestPointsOfOneName:
    def test_two_points_stay_two(self):
        """Two hamlets of one name 300 m apart, on either side of a gmina border, are two places."""
        first, second = point(osm_id=1), point(x=19.344, osm_id=2)

        BaseImporter._merge_places()

        assert fresh(first).is_merged is False
        assert fresh(second).is_merged is False

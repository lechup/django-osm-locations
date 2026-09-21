"""
The seat of an administration, which need not be inside it.

Poland's ring counties are the clear case: powiat krakowski surrounds Kraków
and excludes it, so the county's seat sits in the hole. That point used to be
written into ``center``, and the denormalisation -- which asks which borders
contain a row's centre -- concluded that the county lies inside the city it
encircles. 102 Polish subareas carried an ancestry saying so.
"""
import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon

from django_osm_locations.management.commands.importers.base import BaseImporter
from django_osm_locations.models import CountrySubarea


def ring():
    """A square with a square hole, like a county around its city."""
    outer = ((0, 0), (10, 0), (10, 10), (0, 10), (0, 0))
    hole = ((4, 4), (6, 4), (6, 6), (4, 6), (4, 4))
    return MultiPolygon(Polygon(outer, hole))


MEMBERS_WITH_BOTH = [
    (111, 'n', 'admin_centre'),
    (222, 'n', 'label'),
]


class TestRoleSplitting:
    def test_the_label_is_preferred_for_the_centre(self):
        """A label is placed inside the polygon; a seat need not be."""
        assert BaseImporter._get_center_node_id_from_members(MEMBERS_WITH_BOTH) == 222

    def test_the_seat_comes_from_admin_centre(self):
        assert BaseImporter._get_admin_centre_node_id_from_members(MEMBERS_WITH_BOTH) == 111

    def test_admin_centre_is_still_a_fallback_for_the_centre(self):
        """Most relations have no label, and their seat is usually inside."""
        assert BaseImporter._get_center_node_id_from_members([(111, 'n', 'admin_centre')]) == 111

    def test_a_relation_naming_no_seat_has_none(self):
        assert BaseImporter._get_admin_centre_node_id_from_members([(9, 'w', 'outer')]) is None


class TestSeatDefaults:
    @pytest.fixture
    def importer(self):
        instance = BaseImporter.__new__(BaseImporter)
        instance.nodes = {111: ({'name': 'Kraków'}, (5.0, 5.0))}
        return instance

    def test_point_name_and_node_id_are_captured(self, importer):
        assert importer._seat_defaults(MEMBERS_WITH_BOTH) == {
            'seat': Point(5.0, 5.0),
            'seat_name': 'Kraków',
            'seat_osm_node_id': 111,
        }

    def test_nothing_is_written_when_there_is_no_seat(self, importer):
        assert importer._seat_defaults([(9, 'w', 'outer')]) == {}

    def test_a_seat_whose_node_was_not_cached_is_skipped(self, importer):
        assert importer._seat_defaults([(999, 'n', 'admin_centre')]) == {}


@pytest.mark.django_db
class TestCentreStaysInside:
    def test_a_centre_in_the_hole_is_replaced(self):
        """The seat of a ring county is exactly this case."""
        subarea = CountrySubarea(name='powiat krakowski', border=ring(), center=Point(5.0, 5.0))
        subarea.save()

        assert subarea.border.contains(subarea.center)
        assert subarea.center.coords != (5.0, 5.0)

    def test_a_centre_already_inside_is_kept(self):
        subarea = CountrySubarea(name='somewhere', border=ring(), center=Point(1.0, 1.0))
        subarea.save()

        assert subarea.center.coords == (1.0, 1.0)

    def test_the_seat_itself_is_left_where_it_is(self):
        """It is a fact about the world, not a point that has to be inside."""
        subarea = CountrySubarea(
            name='powiat krakowski', border=ring(),
            seat=Point(5.0, 5.0), seat_name='Kraków', seat_osm_node_id=111,
        )
        subarea.save()

        assert subarea.seat.coords == (5.0, 5.0)
        assert not subarea.border.contains(subarea.seat)


@pytest.mark.django_db
class TestAreaFollowsTheBorder:
    def test_a_new_border_updates_a_stale_area(self):
        """
        The old guard only computed when area was unset, so a re-import wrote
        a new border and left the previous area beside it.
        """
        subarea = CountrySubarea.objects.create(name='x', border=ring())
        first = subarea.area
        assert first > 0

        subarea.border = MultiPolygon(Polygon(((0, 0), (1, 0), (1, 1), (0, 1), (0, 0))))
        subarea.save()

        assert subarea.area != first

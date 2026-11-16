"""
Collapsing a division that is really the place inside it.

A Polish city with county rights exists three times over: as a place, as a
level 6 city-county and as a level 7 gmina, all sharing the city's name. Only
one of them should reach the search index.
"""
import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon

from django_osm_locations.management.commands.importers.poland import PolandImporter
from django_osm_locations.models import CountrySubarea, Place

pytestmark = pytest.mark.django_db


def box(x0, y0, x1, y1):
    return MultiPolygon(Polygon(((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))))


class TestMergingASubareaIntoItsPlace:
    def test_an_identical_border_still_merges(self):
        """The old rule, kept working: inland cities match exactly."""
        border = box(0, 0, 1, 1)
        Place.objects.create(name='Radom', border=border)
        subarea = CountrySubarea.objects.create(name='Radom', border=border)

        PolandImporter._merge_country_subareas(subarea)

        subarea.refresh_from_db()
        assert subarea.is_merged is True

    def test_a_larger_border_round_the_same_city_merges(self):
        """
        Gdańsk: the county takes in the bay and the port, 682 km² against the
        place's 396, so the borders are nowhere near equal.
        """
        Place.objects.create(name='Gdańsk', border=box(0, 0, 1, 1))
        subarea = CountrySubarea.objects.create(name='Gdańsk', border=box(-1, -1, 2, 2))

        PolandImporter._merge_country_subareas(subarea)

        subarea.refresh_from_db()
        assert subarea.is_merged is True

    def test_a_county_containing_a_differently_named_town_is_left_alone(self):
        """
        powiat gdański surrounds Gdańsk without being it. Without the name
        check, containment alone would swallow every county holding a town.
        """
        Place.objects.create(name='Gdańsk', border=box(0, 0, 1, 1))
        subarea = CountrySubarea.objects.create(name='powiat gdański', border=box(-1, -1, 2, 2))

        PolandImporter._merge_country_subareas(subarea)

        subarea.refresh_from_db()
        assert subarea.is_merged is False

    def test_a_place_of_the_same_name_somewhere_else_does_not_count(self):
        """There is a hamlet called Gdańsk in Warmia, 100km away."""
        Place.objects.create(name='Gdańsk', border=box(10, 10, 11, 11))
        subarea = CountrySubarea.objects.create(name='Gdańsk', border=box(0, 0, 1, 1))

        PolandImporter._merge_country_subareas(subarea)

        subarea.refresh_from_db()
        assert subarea.is_merged is False


class TestATownThatIsOnlyAPoint:
    """Głogów and Trzcianka: no level 8 boundary, a level 7 one under the town's name."""

    def town(self, name='Głogów', node=1758659813):
        return Place.objects.create(name=name, center=Point(0.5, 0.5), node_osm_id=node)

    def unit(self, name='Głogów', border=None, seat=1758659813, level=7):
        return CountrySubarea.objects.create(name=name, border=border or box(0, 0, 1, 1),
                                             seat_osm_node_id=seat, admin_level=level)

    def merge(self):
        subareas = CountrySubarea.objects.exclude(border=None)
        PolandImporter._give_seats_the_border_of_their_name(subareas)
        for subarea in subareas:
            PolandImporter._merge_country_subareas(subarea)

    def test_it_takes_the_border_of_the_unit_named_after_it_whose_seat_it_is(self):
        place, gmina = self.town(), self.unit()

        self.merge()

        place.refresh_from_db()
        gmina.refresh_from_db()
        assert place.border is not None and place.border.equals(gmina.border)
        assert gmina.is_merged is True

    def test_the_smallest_such_unit_is_the_one_taken(self):
        """A city-county that is also its gmina: the gmina's border, not the county's with a bay in it."""
        place = self.town()
        gmina = self.unit()
        county = self.unit(border=box(-1, -1, 2, 2), level=6)

        self.merge()

        place.refresh_from_db()
        county.refresh_from_db()
        assert place.border.equals(gmina.border)
        assert county.is_merged is True

    def test_a_gmina_named_gmina_keeps_its_border(self):
        place, gmina = self.town(name='Trzcianka', node=31580123), self.unit(name='gmina Trzcianka', seat=31580123)

        self.merge()

        place.refresh_from_db()
        gmina.refresh_from_db()
        assert place.border is None
        assert gmina.is_merged is False

    def test_a_seat_outside_the_unit_takes_nothing(self):
        """A seat can govern a ring round it, as Kraków does powiat krakowski."""
        place, ring = self.town(), self.unit(border=box(2, 2, 3, 3))

        self.merge()

        place.refresh_from_db()
        assert place.border is None

    def test_a_unit_holding_a_village_with_a_border_stays_a_gmina(self):
        """A gmina tagged under its seat's bare name holds its villages' sołectwa; a town holds none."""
        place, gmina = self.town(), self.unit(border=box(0, 0, 3, 3))
        Place.objects.create(name='Serby', border=box(2, 2, 2.5, 2.5))

        self.merge()

        place.refresh_from_db()
        gmina.refresh_from_db()
        assert place.border is None
        assert gmina.is_merged is False

    def test_a_point_of_the_name_that_is_not_the_seat_takes_nothing(self):
        place, unit = self.town(node=1), self.unit(seat=2)

        self.merge()

        place.refresh_from_db()
        unit.refresh_from_db()
        assert place.border is None
        assert unit.is_merged is False


class TestANameInTwoLanguages:
    def named(self, **tags):
        tags.setdefault('place', 'town')
        PolandImporter._take_polish_name(tags)
        return tags['name']

    def test_the_polish_part_is_the_name(self):
        assert self.named(name='Wilamowice - Wymysoü', **{'name:pl': 'Wilamowice', 'name:wym': 'Wymysoü'}) == 'Wilamowice'

    def test_a_polish_name_that_says_something_else_is_left_alone(self):
        assert self.named(name='Borki', **{'name:pl': 'Suków Borki'}) == 'Borki'

    def test_a_street_keeps_its_name(self):
        """Its addresses say addr:street with the whole name, and are matched to it by that."""
        tags = {'name': 'Ulica A - B', 'name:pl': 'Ulica A', 'highway': 'residential'}
        PolandImporter._take_polish_name(tags)
        assert tags['name'] == 'Ulica A - B'

    def test_no_polish_name_changes_nothing(self):
        assert self.named(name='Wilamowice - Wymysoü') == 'Wilamowice - Wymysoü'

    def test_the_import_renames_relations_ways_and_nodes(self):
        importer = PolandImporter.__new__(PolandImporter)
        importer.relations = {1: ({'name': 'A - B', 'name:pl': 'A', 'boundary': 'administrative'}, [])}
        importer.ways = {2: ({'name': 'C / D', 'name:pl': 'C', 'place': 'village'}, [])}
        importer.nodes = {3: ({'name': 'E - F', 'name:pl': 'E', 'place': 'town'}, (0.0, 0.0))}

        importer.before_import()

        assert [store[key][0]['name'] for store, key in
                ((importer.relations, 1), (importer.ways, 2), (importer.nodes, 3))] == ['A', 'C', 'E']

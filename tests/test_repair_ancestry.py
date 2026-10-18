"""
Repairing the ancestry of packs built before the recalculation was fixed.

The cases are the ones found in Poland's pack: Kazimierz listed under itself
and under Stare Miasto twice, powiat krakowski under gmina Zabierzów.
"""
from django_osm_locations.management.commands.locations_repair_ancestry import repaired
from django_osm_locations.search.indexes import clean_ancestry


def entry(level, slug, name=None):
    return {'level': level, 'slug': slug, 'name': name or slug}


KRAKOW = [entry('country', 'polska'), entry('countrysubarea', 'wojewodztwo-malopolskie'),
          entry('place', 'krakow')]


class TestCleanAncestry:
    def test_a_repeat_is_kept_once_in_its_first_place(self):
        entries = KRAKOW + [entry('placesubarea', 'stare-miasto'), entry('placesubarea', 'stare-miasto')]
        assert clean_ancestry(entries) == KRAKOW + [entry('placesubarea', 'stare-miasto')]

    def test_the_location_itself_is_dropped(self):
        entries = KRAKOW + [entry('placesubarea', 'kazimierz')]
        assert clean_ancestry(entries, 'placesubarea', 'kazimierz') == KRAKOW

    def test_a_namesake_on_another_level_stays(self):
        """Kraków the place under Kraków the county is not itself."""
        entries = [entry('countrysubarea', 'krakow'), entry('place', 'krakow')]
        assert clean_ancestry(entries, 'place', 'krakow') == [entry('countrysubarea', 'krakow')]


class TestRepaired:
    def test_kazimierz(self):
        source = {'slug': 'kazimierz', 'area': 1.0, 'ancestry': KRAKOW + [
            entry('placesubarea', 'stare-miasto'), entry('placesubarea', 'kazimierz'),
            entry('placesubarea', 'stare-miasto'), entry('placesubarea', 'kazimierz')]}
        fields = repaired(source, 'placesubarea', {'stare-miasto': 5.0, 'kazimierz': 1.0})
        assert fields['ancestry'] == KRAKOW + [entry('placesubarea', 'stare-miasto')]
        assert fields['ancestry_slugs'] == [
            'polska', 'wojewodztwo-malopolskie', 'krakow', 'stare-miasto']

    def test_a_smaller_unit_of_the_same_level_is_no_ancestor(self):
        source = {'slug': 'powiat-krakowski', 'area': 1200.0, 'ancestry': [
            entry('countrysubarea', 'wojewodztwo-malopolskie'),
            entry('countrysubarea', 'gmina-zabierzow')]}
        areas = {'wojewodztwo-malopolskie': 15000.0, 'gmina-zabierzow': 100.0}
        fields = repaired(source, 'countrysubarea', areas)
        assert fields['ancestry'] == [entry('countrysubarea', 'wojewodztwo-malopolskie')]

    def test_nothing_to_change_writes_nothing(self):
        source = {'slug': 'kazimierz', 'area': 1.0, 'ancestry': KRAKOW}
        assert repaired(source, 'placesubarea', {}) is None

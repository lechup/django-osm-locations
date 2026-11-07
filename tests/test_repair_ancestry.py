"""
Repairing the ancestry of packs built before the recalculation was fixed.

The cases are the ones found in Poland's pack: Kazimierz listed under itself
and under Stare Miasto twice, powiat krakowski under gmina Zabierzów.
"""
from django.core.management import call_command

from django_osm_locations.management.commands import locations_repair_ancestry
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


class FakeIndices:
    def __init__(self, present):
        self.present, self.merged = present, []

    def exists(self, index):
        return index in self.present

    def forcemerge(self, index, only_expunge_deletes):
        assert index in self.present, 'no such index [{}]'.format(index)
        self.merged.append(index)


class FakeClient:
    def __init__(self, present):
        self.indices = FakeIndices(present)

    def search(self, index, **kwargs):
        return {'hits': {'hits': []}, '_scroll_id': 'scroll'}

    def clear_scroll(self, scroll_id):
        pass


class TestAPackWithoutEveryIndex:
    """The United States' own pack is the country alone; the run stopped on it."""

    def run(self, monkeypatch, present):
        client = FakeClient(present)
        monkeypatch.setattr(locations_repair_ancestry, 'get_client', lambda **kwargs: client)
        monkeypatch.setattr(locations_repair_ancestry, 'get_index_prefix', lambda: 'locations')
        call_command('locations_repair_ancestry')
        return client.indices.merged

    def test_the_country_alone(self, monkeypatch):
        assert self.run(monkeypatch, {'locations-countries'}) == []

    def test_what_is_there_is_still_merged(self, monkeypatch):
        assert self.run(monkeypatch, {'locations-countries', 'locations-places'}) == ['locations-places']

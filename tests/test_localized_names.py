"""
Serving names in the language the caller asked for.

OSM carries translations as ``name:<lang>``, and coverage runs opposite to row
count: a country relation has 200-270, a voivodeship about 95, while 7-10% of
places and 0.5% of streets have any at all. The rows people most want
translated are the ones that carry translations.
"""
import pytest

from django_osm_locations.api.localize import (
    LEVEL_TO_INDEX,
    ancestor_keys,
    apply_language,
    fetch_ancestor_names,
    localized_name,
)
from django_osm_locations.management.commands.importers.base import (
    NON_LANGUAGE_NAME_KEYS,
    BaseImporter,
    language_names,
)
from django_osm_locations.search.indexes import BASE_PROPERTIES, names_all, place_names


class TestGrammarIsNotALanguage:
    """
    ``name:genitive`` is the most common ``name:*`` tag in the Poland extract
    -- 49,118 of them, ahead of every real language. Taking everything after
    the colon would index Polish noun declensions as locales.
    """

    def test_a_declension_is_not_a_locale(self):
        assert language_names({'name:genitive': 'Niemiec'}) == {}

    @pytest.mark.parametrize('suffix', ['genitive', 'prefix', 'adjective',
                                        'etymology', 'start_date', 'wikipedia'])
    def test_every_known_impostor_is_rejected(self, suffix):
        assert language_names({'name:{}'.format(suffix): 'x'}) == {}
        assert suffix in NON_LANGUAGE_NAME_KEYS

    def test_real_languages_survive(self):
        assert language_names({
            'name': 'Deutschland', 'name:pl': 'Niemcy', 'name:en': 'Germany',
        }) == {'pl': 'Niemcy', 'en': 'Germany'}

    def test_a_script_or_region_subtag_is_a_language(self):
        assert language_names({'name:zh-Hant': '德國'}) == {'zh-Hant': '德國'}

    def test_something_unrecognised_is_skipped_rather_than_guessed(self):
        assert language_names({'name:some_new_convention': 'x'}) == {}

    def test_an_empty_translation_is_not_stored(self):
        assert language_names({'name:pl': ''}) == {}


class TestTheParserKeepsThem:
    """
    They cannot be whitelisted by name -- there are 380 such keys in the
    Poland extract -- so the filter has to be a predicate.
    """

    def test_a_language_name_survives_the_tag_filter(self):
        assert BaseImporter._wanted_tag('name:pl') is True

    def test_grammar_is_dropped_before_it_is_ever_cached(self):
        assert BaseImporter._wanted_tag('name:genitive') is False

    def test_the_plain_name_still_survives(self):
        assert BaseImporter._wanted_tag('name') is True

    def test_unrelated_tags_are_still_dropped(self):
        assert BaseImporter._wanted_tag('surface') is False

    def test_the_filter_removes_what_it_should(self):
        tags = {'name': 'x', 'name:pl': 'y', 'name:genitive': 'z', 'surface': 'asphalt'}
        BaseImporter._tag_filter(tags)
        assert tags == {'name': 'x', 'name:pl': 'y'}


class TestIndexShape:
    def test_translations_are_stored_but_not_indexed(self):
        """
        A country carries 200-270 of them; neither 270 mapping fields nor 270
        inverted-index entries per country would earn their keep.
        """
        assert BASE_PROPERTIES['names'] == {'type': 'object', 'enabled': False}

    def test_one_searchable_field_holds_every_language(self):
        assert BASE_PROPERTIES['names_all']['type'] == 'text'
        assert BASE_PROPERTIES['names_all']['analyzer'] == 'location_prefix'

    def test_one_value_per_translation(self):
        """Joined into one string, "Warsaw" was one word of Warszawa's 200 names."""
        names = {'en': 'Warsaw', 'de': 'Warschau', 'nl': 'Warschau', 'fr': 'Varsovie', 'sv': ''}
        assert names_all(names) == ['Varsovie', 'Warsaw', 'Warschau']

    @pytest.mark.parametrize('names', [None, {}, {'en': ''}])
    def test_none_without_translations(self, names):
        assert names_all(names) is None

    def test_a_whole_translation_and_each_word_of_one_without_norms(self):
        """How many translations a place has must not count against it."""
        fields = BASE_PROPERTIES['names_all']['fields']
        assert fields['exact'] == {'type': 'text', 'analyzer': 'location_exact', 'norms': False}
        assert fields['words'] == {'type': 'text', 'analyzer': 'location_prefix_search',
                                   'norms': False}

    @pytest.mark.parametrize('field', ['name', 'ancestry_names'])
    def test_whole_words_beside_the_prefixes(self, field):
        """"Lublin" whole is not the start of Lubliniec."""
        assert BASE_PROPERTIES[field]['analyzer'] == 'location_prefix'
        assert BASE_PROPERTIES[field]['fields']['words'] == {
            'type': 'text', 'analyzer': 'location_prefix_search'}

    def test_the_places_among_the_ancestors_in_whole_words(self):
        """Madrid the city, apart from Comunidad de Madrid round it."""
        assert BASE_PROPERTIES['place_names'] == {'type': 'text', 'analyzer': 'location_prefix_search'}
        entries = [{'level': 'country', 'name': 'España'},
                   {'level': 'countrysubarea', 'name': 'Comunidad de Madrid'},
                   {'level': 'place', 'name': 'Madrid'},
                   {'level': 'placesubarea', 'name': 'Centro'}]
        assert place_names(entries) == ['Madrid']
        assert place_names(entries[:2]) is None


class TestDisplay:
    def test_the_asked_for_language_wins(self):
        assert localized_name({'name': 'Deutschland', 'names': {'pl': 'Niemcy'}}, 'pl') == 'Niemcy'

    def test_an_untranslated_name_falls_back(self):
        """0.5% of streets have any translation at all."""
        assert localized_name({'name': 'Abbey Road', 'names': {}}, 'pl') == 'Abbey Road'

    def test_no_language_asked_for_changes_nothing(self):
        assert localized_name({'name': 'Danmark', 'names': {'pl': 'Dania'}}, None) == 'Danmark'


class TestAncestry:
    def _hit(self):
        return {
            'name': 'Birmingham', 'names': {'pl': 'Birmingham'},
            'ancestry': [
                {'level': 'country', 'slug': 'united-kingdom', 'name': 'United Kingdom'},
                {'level': 'countrysubarea', 'slug': 'england', 'name': 'England'},
            ],
            'ancestry_names': ['United Kingdom', 'England'],
        }

    def test_ancestors_are_collected_once_each(self):
        """Twenty streets in one town share its place, subarea and country."""
        hits = [self._hit(), self._hit()]
        assert ancestor_keys(hits) == {
            ('countries', 'united-kingdom'), ('country-subareas', 'england'),
        }

    def test_every_level_maps_to_an_index(self):
        from django_osm_locations.search.indexes import INDEXES
        assert set(LEVEL_TO_INDEX.values()) <= set(INDEXES)

    def test_ancestry_is_translated(self):
        hits = [self._hit()]
        apply_language(hits, 'pl', {
            ('countries', 'united-kingdom'): 'Wielka Brytania',
            ('country-subareas', 'england'): 'Anglia',
        })
        assert hits[0]['ancestry_names'] == ['Wielka Brytania', 'Anglia']

    def test_an_untranslated_ancestor_keeps_its_name(self):
        hits = [self._hit()]
        apply_language(hits, 'pl', {('countries', 'united-kingdom'): 'Wielka Brytania'})
        assert hits[0]['ancestry_names'] == ['Wielka Brytania', 'England']

    def test_slugs_are_identifiers_and_do_not_move(self):
        hits = [self._hit()]
        apply_language(hits, 'pl', {('countries', 'united-kingdom'): 'Wielka Brytania'})
        assert [e['slug'] for e in hits[0]['ancestry']] == ['united-kingdom', 'england']

    def test_without_a_language_nothing_is_touched(self):
        hits = [self._hit()]
        before = hits[0]['ancestry_names'][:]
        apply_language(hits, None, {})
        assert hits[0]['ancestry_names'] == before


class TestAnAddressKeepsItsName:
    """
    With lang=pl "Marszałkowska 81" came back as "Żabka": an address carries
    the translations of whatever was mapped at it, and they are not its name.
    """

    def _address(self):
        return {'type': 'addresses', 'name': 'Marszałkowska 81', 'names': {'pl': 'Żabka'},
                'ancestry': [{'level': 'country', 'slug': 'polska', 'name': 'Polska'}]}

    def test_it_is_not_renamed(self):
        hit = self._address()
        apply_language([hit], 'pl', {})
        assert hit['name'] == 'Marszałkowska 81'

    def test_its_ancestry_is_still_translated(self):
        hit = self._address()
        apply_language([hit], 'en', {('countries', 'polska'): 'Poland'})
        assert hit['name'] == 'Marszałkowska 81'
        assert hit['ancestry_names'] == ['Poland']

    def test_anything_else_is(self):
        hit = {'type': 'streets', 'name': 'Marszałkowska', 'names': {'en': 'Marshal Street'}}
        apply_language([hit], 'en', {})
        assert hit['name'] == 'Marshal Street'


class FakeSearch:
    """Answers a slug query with the names it was given, and counts the asking."""

    def __init__(self, names, fail=False):
        self.names, self.fail, self.asked = names, fail, []

    def search(self, index, body, ignore_unavailable):
        slugs = body['query']['terms']['slug']
        self.asked.append((index, sorted(slugs)))
        if self.fail:
            raise RuntimeError('no such index')
        return {'hits': {'hits': [
            {'_source': {'slug': slug, 'names': self.names[slug]}}
            for slug in slugs if slug in self.names]}}


class TestAncestorNamesAreRemembered:
    """
    An ancestor's document is read whole to give its names, border and all,
    and Poland is an ancestor of every hit in Poland: lang=pl took the median
    suggestion from 10 ms to 190 ms.
    """

    KEYS = {('countries', 'polska'), ('places', 'krakow')}
    NAMES = {'polska': {'en': 'Poland', 'de': 'Polen'}, 'krakow': {'de': 'Krakau'}}

    @pytest.fixture(autouse=True)
    def empty_cache(self):
        from django.core.cache import cache

        cache.clear()
        yield
        cache.clear()

    def test_asked_once(self):
        client = FakeSearch(self.NAMES)
        first = fetch_ancestor_names(client, self.KEYS, 'en', 'p')
        second = fetch_ancestor_names(client, self.KEYS, 'en', 'p')
        assert first == second == {('countries', 'polska'): 'Poland'}
        assert len(client.asked) == 2    # an index each, the first time only

    def test_an_ancestor_without_a_translation_is_remembered_as_that(self):
        client = FakeSearch(self.NAMES)
        fetch_ancestor_names(client, {('places', 'krakow')}, 'en', 'p')
        assert fetch_ancestor_names(client, {('places', 'krakow')}, 'en', 'p') == {}
        assert len(client.asked) == 1

    def test_only_what_is_not_known_is_asked(self):
        client = FakeSearch(self.NAMES)
        fetch_ancestor_names(client, {('countries', 'polska')}, 'de', 'p')
        found = fetch_ancestor_names(client, self.KEYS, 'de', 'p')
        assert found == {('countries', 'polska'): 'Polen', ('places', 'krakow'): 'Krakau'}
        assert client.asked == [('p-countries', ['polska']), ('p-places', ['krakow'])]

    def test_each_language_and_each_prefix_on_its_own(self):
        client = FakeSearch(self.NAMES)
        assert fetch_ancestor_names(client, {('countries', 'polska')}, 'en', 'p') == {
            ('countries', 'polska'): 'Poland'}
        assert fetch_ancestor_names(client, {('countries', 'polska')}, 'de', 'p') == {
            ('countries', 'polska'): 'Polen'}
        fetch_ancestor_names(client, {('countries', 'polska')}, 'de', 'other')
        assert len(client.asked) == 3

    def test_a_failure_is_not_remembered(self):
        broken = FakeSearch(self.NAMES, fail=True)
        assert fetch_ancestor_names(broken, {('countries', 'polska')}, 'en', 'p') == {}
        working = FakeSearch(self.NAMES)
        assert fetch_ancestor_names(working, {('countries', 'polska')}, 'en', 'p') == {
            ('countries', 'polska'): 'Poland'}

    def test_no_timeout_asks_each_time(self, settings):
        settings.LOCATIONS_ANCESTOR_NAMES_CACHE_TIMEOUT = 0
        client = FakeSearch(self.NAMES)
        fetch_ancestor_names(client, {('countries', 'polska')}, 'en', 'p')
        fetch_ancestor_names(client, {('countries', 'polska')}, 'en', 'p')
        assert len(client.asked) == 2

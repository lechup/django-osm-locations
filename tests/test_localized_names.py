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
    localized_name,
)
from django_osm_locations.management.commands.importers.base import (
    NON_LANGUAGE_NAME_KEYS,
    BaseImporter,
    language_names,
)
from django_osm_locations.search.indexes import BASE_PROPERTIES


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

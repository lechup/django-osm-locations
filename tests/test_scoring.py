"""
Relevance scoring for cross-type search.

Text scores alone put the three districts named Warszawa above the city of 1.8
million, and tied Kraków with the village of Krakuszowice, because nothing in a
text match knows what kind of thing it matched or how big it is.
"""
import pytest

from django_osm_locations.api.queries import (
    DEFAULT_TYPE_BOOSTS,
    apply_scoring,
    build_body,
    type_boosts,
)


class TestTypeBoosts:
    def test_a_place_outranks_a_subdivision_of_one(self):
        assert DEFAULT_TYPE_BOOSTS['places'] > DEFAULT_TYPE_BOOSTS['place-subareas']

    def test_a_place_outranks_the_county_sharing_its_name(self):
        """Poland has a city of Gdańsk and a county of Gdańsk."""
        assert DEFAULT_TYPE_BOOSTS['places'] > DEFAULT_TYPE_BOOSTS['country-subareas']

    def test_streets_rank_lowest(self):
        assert DEFAULT_TYPE_BOOSTS['streets'] == min(DEFAULT_TYPE_BOOSTS.values())

    def test_every_type_has_a_boost(self):
        from django_osm_locations.search.indexes import INDEXES
        assert set(DEFAULT_TYPE_BOOSTS) == set(INDEXES)

    def test_settings_can_override(self, settings):
        settings.LOCATIONS_TYPE_BOOSTS = {'streets': 99.0}
        assert type_boosts() == {'streets': 99.0}


class TestScoringShape:
    @pytest.fixture
    def scored(self):
        return apply_scoring({'match_all': {}})['function_score']

    def test_boosts_multiply_rather_than_add(self, scored):
        """
        An additive boost competes with the text score, so its effect depends on
        how well the text matched; a multiplier keeps the ordering stable.
        """
        assert scored['boost_mode'] == 'multiply'
        assert scored['score_mode'] == 'multiply'

    def test_the_original_query_is_preserved(self, scored):
        assert scored['query'] == {'match_all': {}}

    def test_one_weight_per_type(self, scored):
        weighted = [f for f in scored['functions'] if 'weight' in f]
        assert len(weighted) == len(DEFAULT_TYPE_BOOSTS)

    def test_size_breaks_ties_between_equal_text_matches(self, scored):
        """"krak" matches Kraków and Krakuszowice identically."""
        fields = [
            f['field_value_factor']['field']
            for f in scored['functions'] if 'field_value_factor' in f
        ]
        assert 'population' in fields
        assert 'area' in fields, 'population is sparse in OSM, so area is the fallback'

    def test_size_is_damped(self, scored):
        """Size should nudge the order, not dictate it."""
        for f in scored['functions']:
            if 'field_value_factor' in f:
                assert f['field_value_factor']['modifier'].startswith('log')

    def test_missing_size_does_not_zero_the_score(self, scored):
        """log2p(0) is non-zero; log1p(0) would annihilate streets."""
        for f in scored['functions']:
            if 'field_value_factor' in f:
                assert f['field_value_factor']['modifier'] == 'log2p'


class TestWhenScoringApplies:
    def test_applied_when_there_is_a_query(self):
        body, _, _ = build_body({'q': 'krakow'})
        assert 'function_score' in body['query']

    def test_not_applied_without_a_query(self):
        """Nothing is being scored; results are sorted by size instead."""
        body, _, _ = build_body({})
        assert 'function_score' not in body['query']

    def test_not_applied_to_a_pure_filter(self):
        body, _, _ = build_body({'within': 'krakow'})
        assert 'function_score' not in body['query']

"""
Relevance scoring for cross-type search.

Text scores alone put the three districts named Warszawa above the city of 1.8
million, and tied Kraków with the village of Krakuszowice, because nothing in a
text match knows what kind of thing it matched or how big it is.
"""
import pytest

from django_osm_locations.api.queries import (
    DEFAULT_TYPE_BOOSTS,
    EXACT_NAME_WEIGHT,
    apply_scoring,
    build_body,
    exact_name_functions,
    type_boosts,
)


class TestTypeBoosts:
    def test_a_place_outranks_a_subdivision_of_one(self):
        assert DEFAULT_TYPE_BOOSTS['places'] > DEFAULT_TYPE_BOOSTS['place-subareas']

    def test_a_division_outranks_the_places_inside_it(self):
        """
        Searching a region's name should reach the region: "ireland" offers
        Northern Ireland before Belfast.

        This is the deliberate reverse of what it was. Poland has a city of
        Gdańsk and a county of Gdańsk, and the city used to win; it no longer
        does. LOCATIONS_TYPE_BOOSTS is how a deployment puts that back.
        """
        assert DEFAULT_TYPE_BOOSTS['country-subareas'] > DEFAULT_TYPE_BOOSTS['places']

    def test_addresses_rank_lowest(self):
        """
        Below streets on purpose: 8.5 million addresses for Poland against 1.9
        million streets, so searching "Lakowa" would otherwise bury the street
        under every house on it.
        """
        assert DEFAULT_TYPE_BOOSTS['addresses'] == min(DEFAULT_TYPE_BOOSTS.values())

    def test_a_street_outranks_its_own_addresses(self):
        assert DEFAULT_TYPE_BOOSTS['streets'] > DEFAULT_TYPE_BOOSTS['addresses']

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


class TestAWholeNameAmongTheWordsTyped:
    def test_each_run_of_words_is_tried_whole(self):
        """Friedrichshain, before Friedrichshain-Kreuzberg, for "Berlin, Friedrichshain"."""
        (function,) = exact_name_functions('Berlin, Friedrichshain')
        matches = function['filter']['bool']['should']
        assert {'match': {'name.exact': 'Friedrichshain'}} in matches
        assert {'match': {'names_all.exact': 'Friedrichshain'}} in matches
        assert {'match': {'name.exact': 'Berlin'}} in matches

    def test_it_multiplies_as_the_type_does(self):
        """Added to the text score, no amount put the district Butel before Opština Butel."""
        functions = apply_scoring({'match_all': {}}, 'Skopje, Butel')['function_score']['functions']
        weights = [f['weight'] for f in functions if 'match' in str(f.get('filter'))]
        assert weights == [EXACT_NAME_WEIGHT]
        assert EXACT_NAME_WEIGHT > 1

    def test_nothing_without_a_query(self):
        assert exact_name_functions(None) == []

    def test_nor_for_the_whole_query(self):
        """Typed whole, "Vila" is as likely the start of Vila Nova de Gaia; name.exact weighs it."""
        assert exact_name_functions('Vila') == []
        (function,) = exact_name_functions('Berlin, Friedrichshain')
        runs = {m['match'].get('name.exact') for m in function['filter']['bool']['should']}
        assert 'Berlin Friedrichshain' not in runs

    def test_a_number_alone_is_not_tried(self):
        (function,) = exact_name_functions('Floriańska 14, Kraków')
        runs = {m['match'].get('name.exact') for m in function['filter']['bool']['should']}
        assert '14' not in runs and 'Floriańska 14' in runs


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


class TestSizeFallback:
    """
    Population where there is one, area otherwise -- never both.

    Appending the two unfiltered made them compound, so a hit with both signals
    beat one with a single larger signal. Against the real index Belfast (a
    node, 333,000 people, no polygon) scored 8.3 while Fermanagh and Omagh
    District (13,757 people, 3,021 km²) scored 72.0, purely because the
    district had an area to multiply by. Most places are nodes -- 97.9% of the
    imported UK ones have no border -- so this decided the common case.
    """

    @pytest.fixture
    def size_functions(self):
        functions = apply_scoring({'match_all': {}})['function_score']['functions']
        return [f for f in functions if 'field_value_factor' in f]

    def test_population_its_places_and_area_are_all_offered(self, size_functions):
        assert {f['field_value_factor']['field'] for f in size_functions} == {
            'population', 'place_population', 'area'}

    def test_each_size_function_is_filtered(self, size_functions):
        """An unfiltered one would apply to every hit and compound with the other."""
        for function in size_functions:
            assert 'filter' in function, function['field_value_factor']['field']

    def test_the_filters_are_complementary(self, size_functions):
        """Anything else and the two compound, which is the old bug."""
        by_field = {f['field_value_factor']['field']: f['filter'] for f in size_functions}
        has_population = {'range': {'population': {'gt': 0}}}
        in_a_place = {'range': {'place_population': {'gt': 0}}}
        assert by_field['population'] == has_population
        assert by_field['place_population'] == {
            'bool': {'filter': [in_a_place], 'must_not': [has_population]}}
        assert by_field['area'] == {'bool': {'must_not': [has_population, in_a_place]}}

    def test_a_street_and_a_district_rank_by_the_place_they_are_in(self, size_functions):
        """
        A street has no size and few districts have people of their own: every
        Marszałkowska scored alike and Warszawa's came 13th, and Nowa Huta in
        Kraków after eight villages of the name.
        """
        by_field = {f['field_value_factor']['field']: f for f in size_functions}
        assert by_field['place_population']['field_value_factor']['modifier'] == 'log2p'

    def test_sizes_still_multiply_with_the_type_boost(self):
        assert apply_scoring({'match_all': {}})['function_score']['score_mode'] == 'multiply'

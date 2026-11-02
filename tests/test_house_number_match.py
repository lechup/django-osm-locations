"""
The house number typed is the one that comes first.

The number is matched as a word of the name, and names are indexed as
prefixes, so "14" is found in Tadeusza Gajcego 142 as much as in 14 and the
two scored the same. Against the Poland pack Gajcego 14 in Łódź came seventh,
behind 140-148 and 146A; Romanowskiego 5 in Lublin eighth, behind 50-58; and
Floriańska 1 in Kraków not in the first twelve.
"""
import re

import pytest

from django_osm_locations.api.queries import (
    DEFAULT_TYPE_BOOSTS,
    HOUSE_NUMBER_SORT_MAX,
    HOUSE_NUMBER_WEIGHTS,
    POSTCODE_WEIGHT,
    apply_scoring,
    build_body,
    house_number_functions,
)
from django_osm_locations.search.indexes import INDEXES


def matches(clause, doc):
    """Whether a filter clause matches a document, as OpenSearch would decide it."""
    (kind, spec), = clause.items()
    if kind == 'bool':
        return (all(matches(c, doc) for c in spec.get('filter', []))
                and not any(matches(c, doc) for c in spec.get('must_not', [])))
    (field, value), = spec.items()
    if kind == 'terms':
        return doc.get(field) in value
    if kind == 'term':
        return doc.get(field) == value
    if kind == 'regexp':
        # Lucene's syntax, as far as these use it, is Python's, anchored
        return re.fullmatch(value, doc.get(field) or '') is not None
    raise AssertionError('unexpected clause {}'.format(kind))


def multiplier(q, house_number, house_number_sort=None, street_name='Floriańska'):
    """What the functions multiply an address's score by."""
    if house_number_sort is None:
        house_number_sort = int(re.match(r'\d+', house_number).group())
    doc = {'house_number': house_number, 'house_number_sort': house_number_sort,
           'street_name': street_name}
    value = 1.0
    for function in house_number_functions(q):
        if matches(function['filter'], doc):
            value *= function['weight']
    return value


def leaves(clause):
    """The term-level clauses inside a filter."""
    (kind, spec), = clause.items()
    if kind == 'bool':
        return [leaf for c in spec.get('filter', []) + spec.get('must_not', [])
                for leaf in leaves(c)]
    return [clause]


def typed(q):
    """The keyword forms matched for ``q``."""
    return sorted({form for f in house_number_functions(q) for leaf in leaves(f['filter'])
                   for form in leaf.get('terms', {}).get('house_number', [])})


def leading(q):
    """The leading integers matched for ``q``."""
    return sorted({leaf['term']['house_number_sort'] for f in house_number_functions(q)
                   for leaf in leaves(f['filter']) if 'house_number_sort' in leaf.get('term', {})})


class TestWhatCountsAsAHouseNumber:
    @pytest.mark.parametrize('q,number', [
        ('Tadeusza Gajcego 14, Łódź', '14'),
        ('Łódź, Tadeusza Gajcego 14', '14'),
        ('14 Gajcego Łódź', '14'),
        ('Floriańska 12a Kraków', '12a'),
        ('Grodzka 52A, Kraków', '52A'),
        ('Wiejska 4/6/8, Warszawa', '4/6/8'),
        ('Marszałkowska 29-30 Warszawa', '29-30'),
        ('Mickiewicza 4bis', '4bis'),
        ('ul.12 Długa', '12'),
    ])
    def test_a_run_that_starts_with_a_digit(self, q, number):
        assert number in typed(q)

    @pytest.mark.parametrize('q', [
        'Floriańska, Kraków',
        'Kraków',
        'województwo małopolskie',
        '',
        None,
    ])
    def test_no_number_no_functions(self, q):
        assert house_number_functions(q) == []

    @pytest.mark.parametrize('q', ['A4 Paris', 'B56 Utrecht', 'Kraków2'])
    def test_a_digit_inside_a_word_is_not_one(self, q):
        """A4 in France is a road, not house 4."""
        assert house_number_functions(q) == []

    def test_every_number_in_the_query_counts(self):
        """
        "Aleja 3 Maja 5": the street is named after the 3rd of May, and 5 is
        the house. Both are offered, each to the addresses it can be.
        """
        assert typed('Aleja 3 Maja 5, Kraków') == ['3', '5']
        assert leading('Aleja 3 Maja 5, Kraków') == [3, 5]


class TestTheFormsMatched:
    def test_upper_and_lower_case_both(self):
        """OSM has 12a and 12A, and the keyword is case-sensitive."""
        assert typed('Floriańska 12a') == ['12A', '12a']
        assert typed('Floriańska 12A') == ['12A', '12a']

    def test_a_number_alone_is_matched_as_it_is(self):
        assert typed('Floriańska 14') == ['14']

    def test_the_leading_integer_of_each(self):
        assert leading('Wiejska 4/6/8') == [4]
        assert leading('Floriańska 12a') == [12]
        assert leading('Marszałkowska 29-30') == [29]

    def test_a_number_too_big_for_the_integer_field_is_left_out(self):
        """A term beyond 32 bits is a 400 from OpenSearch, not a miss."""
        assert leading('Floriańska 07700900123') == []
        assert typed('Floriańska 07700900123') == ['07700900123']
        assert leading('Floriańska {}'.format(HOUSE_NUMBER_SORT_MAX)) == [HOUSE_NUMBER_SORT_MAX]
        assert leading('Floriańska {}'.format(HOUSE_NUMBER_SORT_MAX + 1)) == []

    def test_only_fields_an_address_has(self):
        """Every other type lacks all three, so the functions cannot touch it."""
        fields = {field for f in house_number_functions('Aleja 3 Maja 14')
                  for leaf in leaves(f['filter']) for spec in leaf.values() for field in spec}
        assert fields == {'house_number', 'house_number_sort', 'street_name'}
        for name, index in INDEXES.items():
            if name != 'addresses':
                assert not fields & set(index.properties), name
        assert fields <= set(INDEXES['addresses'].properties)


class TestTheOrder:
    def test_the_number_typed_comes_first(self):
        q = 'Tadeusza Gajcego 14, Łódź'
        for other in ('140', '142', '144', '146', '146A', '148'):
            assert multiplier(q, '14') > multiplier(q, other), other

    def test_the_same_number_with_a_letter_comes_next(self):
        """14a and 14/16 are house 14's neighbours, 142 is not."""
        q = 'Tadeusza Gajcego 14'
        assert multiplier(q, '14') > multiplier(q, '14a') > multiplier(q, '142')
        assert multiplier(q, '14/16') == multiplier(q, '14a')

    def test_the_letter_typed_wins_over_the_bare_number(self):
        q = 'Floriańska 12a, Kraków'
        assert multiplier(q, '12a') > multiplier(q, '12')
        assert multiplier(q, '12A') == multiplier(q, '12a')

    def test_a_number_that_only_starts_the_same_gains_nothing(self):
        assert multiplier('Romanowskiego 5, Lublin', '56') == 1.0

    def test_the_weights(self):
        assert multiplier('Floriańska 1', '1') == (HOUSE_NUMBER_WEIGHTS['house_number']
                                                   * HOUSE_NUMBER_WEIGHTS['house_number_sort'])

    def test_they_multiply_rather_than_add(self):
        """
        Like the type weights: added, they would count for more against a weak
        text match than a strong one.
        """
        for function in house_number_functions('Floriańska 1'):
            assert set(function) == {'filter', 'weight'}
            assert function['weight'] > 1

    def test_they_never_lift_an_address_over_a_street(self):
        """
        Together below what a street is worth over an address. At 2.0 and 1.5
        "3 Maja Kraków" put Księdza Franciszka Maja 3 -- house 3, on a street
        with no number to take it -- before every street called 3 Maja.
        """
        both = HOUSE_NUMBER_WEIGHTS['house_number'] * HOUSE_NUMBER_WEIGHTS['house_number_sort']
        assert both < DEFAULT_TYPE_BOOSTS['streets'] / DEFAULT_TYPE_BOOSTS['addresses']


class TestANumberInTheStreetsName:
    """
    Every Polish town has a 3 Maja, an 11 Listopada, an Osiedle 1 Maja.
    "3 Maja Kraków" is the street; weighting house 3 put 3 Maja 3 above it.
    """

    def test_is_not_the_house_number(self):
        assert multiplier('3 Maja Kraków', '3', street_name='3 Maja') == 1.0
        assert multiplier('Aleja 3 Maja, Kraków', '3a', street_name='Aleja 3 Maja') == 1.0
        assert multiplier('1 Maja Łódź', '1', street_name='Osiedle 1 Maja') == 1.0
        assert multiplier('11 Listopada Kraków', '11', street_name='Aleja 11 Listopada') == 1.0

    def test_the_other_number_still_is(self):
        q = 'Aleja 3 Maja 5, Kraków'
        assert multiplier(q, '5', street_name='Aleja 3 Maja') > multiplier(
            q, '50', street_name='Aleja 3 Maja')
        assert multiplier(q, '3', street_name='Aleja 3 Maja') == 1.0

    def test_typed_twice_it_is_both(self):
        """"3 Maja 3" is house 3 on 3 Maja."""
        q = '3 Maja 3 Słomniki'
        assert multiplier(q, '3', street_name='3 Maja') > multiplier(q, '30', street_name='3 Maja')
        assert multiplier(q, '3', street_name='3 Maja') == (
            multiplier('Floriańska 3', '3') * HOUSE_NUMBER_WEIGHTS['typed_twice'])

    def test_and_then_the_house_comes_before_the_street(self):
        """The street 3 Maja has every word of "3 Maja 3", and is not what was asked."""
        assert multiplier('3 Maja 3', '3', street_name='3 Maja') > (
            DEFAULT_TYPE_BOOSTS['streets'] / DEFAULT_TYPE_BOOSTS['addresses'])

    def test_typed_twice_lifts_only_that_number(self):
        q = '3 Maja 3'
        assert multiplier(q, '3a', street_name='3 Maja') == HOUSE_NUMBER_WEIGHTS['house_number_sort']
        assert multiplier(q, '30', street_name='3 Maja') == 1.0

    def test_on_a_street_without_it_the_number_counts(self):
        assert multiplier('3 Maja Kraków', '3', street_name='Księdza Franciszka Maja') > 1.0

    @pytest.mark.parametrize('street', ['Aleja 30-lecia', 'Osiedle 1000-lecia', '13 Pułku',
                                        'Plac 23 Stycznia'])
    def test_only_as_a_whole_number(self, street):
        """3 is not in 30-lecia, nor in 13 Pułku: house 3 there is still house 3."""
        assert multiplier('{} 3'.format(street), '3', street_name=street) > 1.0

    @pytest.mark.parametrize('street', ['3 Maja', 'Aleja 3 Maja', 'Plac Konstytucji 3 Maja',
                                        '3/5 Maja', 'Maja 3'])
    def test_wherever_it_stands_in_the_street(self, street):
        assert multiplier('3 Maja', '3', street_name=street) == 1.0

    def test_a_village_address_has_no_street_to_have_it(self):
        assert multiplier('Kowale 3', '3', street_name='') > 1.0

    @pytest.mark.parametrize('q,street,numbers', [
        ('Straße des 17. Juni Berlin', 'Straße des 17. Juni', ['17']),
        ('Avenue du 8 Mai 1945 Lyon', 'Avenue du 8 Mai 1945', ['8', '1945']),
        ('Place du 14 Juillet Marseille', 'Place du 14 Juillet', ['14']),
        ('Carrer del 31 de Desembre Palma', 'Carrer del 31 de Desembre', ['31']),
        ('Via 11 febbraio Firenze', 'Via 11 febbraio', ['11']),
        ('Plein 1944 Nijmegen', 'Plein 1944', ['1944']),
    ])
    def test_the_dates_of_other_countries(self, q, street, numbers):
        for number in numbers:
            assert multiplier(q, number, street_name=street) == 1.0

    def test_a_house_on_a_street_named_after_a_year(self):
        q = 'Plein 1944 6 Nijmegen'
        assert multiplier(q, '6', street_name='Plein 1944') > multiplier(q, '60', street_name='Plein 1944')
        assert multiplier(q, '1944', street_name='Plein 1944') == 1.0

    def test_the_number_typed_before_the_street(self):
        assert multiplier('10 Downing Street London', '10', street_name='Downing Street') > (
            multiplier('10 Downing Street London', '100', street_name='Downing Street'))


class TestAFlatOrAPostcode:
    """Against the Poland pack 93 of 98 queries with a flat number missed, and every one with a postcode."""

    def test_after_a_slash_the_house_is_matched_as_if_alone(self):
        assert typed('Floriańska 14/3') == ['14', '14/3']
        assert multiplier('Floriańska 14/3', '14') == multiplier('Floriańska 14', '14')

    def test_and_so_is_a_house_numbered_so(self):
        assert multiplier('Floriańska 14/3', '14/3') == multiplier('Floriańska 14', '14')
        assert multiplier('Aleja 1 Maja 31/33', '31/33', street_name='Aleja 1 Maja') > 1.0

    def test_15_a_is_that_house_and_not_house_15(self):
        q = 'Via Girolamo Cocconi 15/a, Roma'
        assert typed(q) == ['15/A', '15/a']
        assert multiplier(q, '15/a', street_name='Via Girolamo Cocconi') > multiplier(
            q, '15', street_name='Via Girolamo Cocconi')

    def test_14a_still_comes_after_14(self):
        assert multiplier('Floriańska 14/3', '14') > multiplier('Floriańska 14/3', '14a')

    @pytest.mark.parametrize('q', ['Floriańska 14 m. 3', 'Floriańska 14 lok. 3'])
    def test_the_flat_is_not_a_house(self, q):
        assert typed(q) == ['14']
        assert multiplier(q, '3') == 1.0

    def test_a_postcode_is_not_a_house(self):
        q = 'Floriańska 14, 31-019 Kraków'
        assert (typed(q), leading(q)) == (['14'], [14])
        assert multiplier(q, '31') == 1.0

    def test_a_postcode_weighs_the_addresses_that_have_it(self):
        functions = apply_scoring({'match_all': {}}, 'Floriańska 14, 31-019 Kraków')['function_score']['functions']
        assert {'filter': {'terms': {'postcode': ['31-019']}}, 'weight': POSTCODE_WEIGHT} in functions

    def test_only_addresses_have_a_postcode(self):
        for name, index in INDEXES.items():
            assert ('postcode' in index.properties) == (name == 'addresses'), name

    def test_with_the_number_it_lifts_the_house_over_the_street(self):
        """Whoever types the number and the postcode means the house."""
        both = HOUSE_NUMBER_WEIGHTS['house_number'] * HOUSE_NUMBER_WEIGHTS['house_number_sort']
        assert both * POSTCODE_WEIGHT > DEFAULT_TYPE_BOOSTS['streets'] / DEFAULT_TYPE_BOOSTS['addresses']


class TestWhereTheyApply:
    def functions(self, params, strict=True):
        body, _, _ = build_body(params, strict=strict)
        return body['query']['function_score']['functions']

    def test_a_query_with_a_number_carries_them(self):
        functions = self.functions({'q': 'Floriańska 14, Kraków'})
        for function in house_number_functions('Floriańska 14, Kraków'):
            assert function in functions

    def test_so_does_the_fallback_to_any_word(self):
        """A typo in the street is no reason to lose the number."""
        functions = self.functions({'q': 'Florianskka 14, Kraków'}, strict=False)
        for function in house_number_functions('Florianskka 14'):
            assert function in functions

    def test_a_query_without_one_does_not(self):
        functions = self.functions({'q': 'Floriańska, Kraków'})
        assert functions == apply_scoring({'match_all': {}})['function_score']['functions']

    def test_nor_does_scoring_without_a_query(self):
        """What test_scoring counts: one weight per type, and the two sizes."""
        functions = apply_scoring({'match_all': {}})['function_score']['functions']
        assert len(functions) == len(DEFAULT_TYPE_BOOSTS) + 2

    def test_the_type_weights_and_sizes_stay(self):
        functions = self.functions({'q': 'Floriańska 14'})
        assert len([f for f in functions if 'field_value_factor' in f]) == 2
        assert {f['filter']['term']['type'] for f in functions
                if 'type' in f['filter'].get('term', {})} == set(INDEXES)

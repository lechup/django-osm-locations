"""
House numbers in the order a person walks the street.

A keyword sorts 1, 10, 11, 2; an integer field cannot hold 12a or 5/7. The
leading integer does the ordering and the raw keyword breaks ties, which
gives 1, 2, 3, 3 B, 5/7, 10, 12, 12a. Measured across seven extracts and 36
million addresses, at most 0.2% in any country has no digit to lead with --
"Unit 10", "Inside WH Smith", Finnish stairwells -- and those sort last.
"""
import pytest
from django.contrib.gis.geos import Point

from django_osm_locations.api.queries import NUMBER_SORT, SIZE_SORT, build_body, build_sort
from django_osm_locations.management.commands.importers.base import BaseImporter
from django_osm_locations.models import Address
from django_osm_locations.search.indexes import INDEXES


class TestTheSortKey:
    @pytest.mark.parametrize('number,key', [
        ('7', 7),                # pure integer          79-94% everywhere
        ('6a', 6),               # integer + letter      2-20%
        ('24B', 24),
        ('3 B', 3),
        ('1/3', 1),              # slash: sorts by the first
        ('29-30', 29),           # range: sorts by the first
        ('4 bis', 4),            # French suffix
        ('2-pomp', 2),           # Dutch suffix
        ('6,7', 6),
        ('  12', 12),            # leading whitespace tolerated
    ])
    def test_the_leading_integer_orders_every_convention(self, number, key):
        assert BaseImporter._house_number_sort_key(number) == key

    @pytest.mark.parametrize('number', [
        'Unit 10', 'Inside WH Smith', 'The Regent',   # British units and landmarks
        'A4', 'B56', 'B 20', 'C 18 III',              # French road, Dutch/Finnish letters
        'ev.43', 'Le Capitole', '', None,
    ])
    def test_no_leading_digit_gives_none_rather_than_a_guess(self, number):
        """
        Stripping a leading letter would misplace these: A4 in France is a
        road, not house 4. They sort last instead.
        """
        assert BaseImporter._house_number_sort_key(number) is None

    @pytest.mark.parametrize('number,key', [
        ('2147483647', 2 ** 31 - 1),     # the largest the column holds
        ('2147483648', None),            # one more no longer fits
        ('07700900123', None),           # a mobile number in addr:housenumber
        ('100012345678 Flat 2', None),   # a property reference
    ])
    def test_digits_too_long_for_the_column_sort_last(self, number, key):
        """One of these stopped Britain's import: "integer out of range"."""
        assert BaseImporter._house_number_sort_key(number) == key

    def test_it_is_derived_at_import(self):
        d = BaseImporter._address_defaults(
            {'addr:housenumber': '12a', 'addr:street': 'Łąkowa'})
        assert d['house_number'] == '12a'
        assert d['house_number_sort'] == 12

    def test_a_country_importer_may_override_it(self):
        """Nothing does today; the seven-country measurement says nothing needs to."""
        assert callable(getattr(BaseImporter, '_house_number_sort_key'))
        assert isinstance(BaseImporter.__dict__['_house_number_sort_key'], classmethod)


class TestTheIndex:
    def test_the_key_is_an_integer_beside_the_keyword(self):
        props = INDEXES['addresses'].properties
        assert props['house_number_sort'] == {'type': 'integer'}
        assert props['house_number'] == {'type': 'keyword'}

    def test_the_document_carries_it(self):
        a = Address(id=1, name='Łąkowa 12a', slug='x', house_number='12a',
                    house_number_sort=12, center=Point(18.6, 54.35), bbox=[])
        assert INDEXES['addresses'].document(a)['house_number_sort'] == 12


class TestTheSortClause:
    def test_order_number_is_leading_integer_then_raw(self):
        assert build_sort('number', False) == NUMBER_SORT
        assert [next(iter(c)) for c in NUMBER_SORT] == ['house_number_sort', 'house_number']

    def test_those_without_a_number_go_last(self):
        assert NUMBER_SORT[0]['house_number_sort']['missing'] == '_last'

    def test_it_survives_an_index_that_lacks_the_field(self):
        """?type=addresses,streets&order=number spans the streets index too."""
        for clause in NUMBER_SORT:
            field, spec = next(iter(clause.items()))
            assert 'unmapped_type' in spec, field

    def test_the_tie_breaker_keeps_12_before_12a(self):
        assert NUMBER_SORT[1]['house_number']['order'] == 'asc'


class TestTheDefaultForAddresses:
    def test_an_addresses_only_request_defaults_to_number(self):
        """
        An address has no size worth the name, so ?within=lakowa-gdansk on
        /api/addresses/ should read the street in walking order unprompted.
        """
        assert build_sort(None, False, ['addresses']) == NUMBER_SORT

    def test_a_query_still_defaults_to_relevance(self):
        assert build_sort(None, True, ['addresses']) == ['_score']

    def test_mixed_types_keep_the_size_default(self):
        """Streets and places have sizes; number would be meaningless for them."""
        assert build_sort(None, False, ['addresses', 'streets']) == SIZE_SORT

    def test_no_types_given_keeps_the_size_default(self):
        assert build_sort(None, False) == SIZE_SORT
        assert build_sort(None, False, None) == SIZE_SORT

    def test_an_explicit_order_wins_over_the_default(self):
        assert build_sort('name', False, ['addresses'])[0].get('name.keyword')

    def test_build_body_threads_the_types_through(self):
        body, _, _ = build_body({}, ['addresses'])
        assert body['sort'] == NUMBER_SORT
        body, _, _ = build_body({}, ['streets'])
        assert body['sort'] == SIZE_SORT

    def test_the_pinned_endpoint_needs_no_type_parameter(self):
        """/api/addresses/ carries no ?type=; the view passes what it is pinned to."""
        body, _, _ = build_body({'within': 'lakowa-gdansk'}, ['addresses'])
        assert body['sort'] == NUMBER_SORT


class TestValuesLongerThanTheirColumns:
    """
    Britain's import stopped with "value too long for type character
    varying(64)": an addr:housenumber listing every flat of a block. One such
    value fails the whole batch it is written with.
    """

    def test_a_list_keeps_its_first_number(self):
        flats = ';'.join(str(n) for n in range(1, 40))
        d = BaseImporter._address_defaults({'addr:housenumber': flats, 'addr:street': 'High Street'})
        assert d['house_number'] == '1'
        assert d['house_number_sort'] == 1
        assert d['name'] == 'High Street 1'

    def test_a_single_number_is_left_alone(self):
        d = BaseImporter._address_defaults({'addr:housenumber': '12a', 'addr:street': 'Łąkowa'})
        assert d['house_number'] == '12a'

    @pytest.mark.parametrize('field,tag,limit', [
        ('house_number', 'addr:housenumber', 64),
        ('postcode', 'addr:postcode', 32),
        ('street_name', 'addr:street', 255),
        ('settlement_name', 'addr:city', 255),
    ])
    def test_every_text_value_fits_its_column(self, field, tag, limit):
        d = BaseImporter._address_defaults({tag: 'x' * (limit + 50), 'addr:housenumber': '1'}
                                           if tag != 'addr:housenumber' else {tag: 'x' * (limit + 50)})
        assert len(d[field]) == limit
        assert len(d['name']) <= 255

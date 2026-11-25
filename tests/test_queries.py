"""
Request parameters to OpenSearch queries.

No server needed: these assert the query body, which is where the API's
behaviour actually lives.
"""
import pytest

from django_osm_locations.api.queries import (
    QueryError,
    build_body,
    build_query,
    build_sort,
    exclude_types,
    parse_bbox,
    parse_near,
    parse_page,
    parse_types,
    parse_within,
    read_query,
    word_runs,
)


class TestTypes:
    def test_defaults_to_every_type(self):
        assert set(parse_types(None)) == {
            'countries', 'country-subareas', 'places', 'place-subareas', 'streets',
            'addresses',
        }

    def test_parses_a_subset(self):
        assert parse_types('places,streets') == ['places', 'streets']

    def test_rejects_unknown_types(self):
        with pytest.raises(QueryError) as exc:
            parse_types('places,villages')
        assert 'villages' in str(exc.value)


class TestExcludedTypes:
    def test_everything_but_addresses(self):
        assert exclude_types(parse_types(None), 'addresses') == [
            name for name in parse_types(None) if name != 'addresses']

    def test_several(self):
        assert exclude_types(parse_types(None), 'addresses, streets') == [
            name for name in parse_types(None) if name not in ('addresses', 'streets')]

    def test_from_the_types_asked_for(self):
        assert exclude_types(parse_types('streets,addresses'), 'addresses') == ['streets']

    def test_a_type_not_searched_changes_nothing(self):
        """/api/streets/?exclude_type=addresses is still the streets."""
        assert exclude_types(['streets'], 'addresses') == ['streets']

    @pytest.mark.parametrize('value', [None, ''])
    def test_absent_changes_nothing(self, value):
        assert exclude_types(['streets', 'addresses'], value) == ['streets', 'addresses']

    def test_rejects_unknown_types(self):
        with pytest.raises(QueryError) as exc:
            exclude_types(parse_types(None), 'villages')
        assert 'villages' in str(exc.value)

    def test_rejects_leaving_nothing(self):
        with pytest.raises(QueryError):
            exclude_types(['streets'], 'streets')


class TestWhatIsTyped:
    """What people in Poland type around an address, and no name carries."""

    @pytest.mark.parametrize('q,required', [
        ('ul. Floriańska 14, Kraków', 'Floriańska 14, Kraków'),
        ('ul.Floriańska 14', 'Floriańska 14'),
        ('UL. Floriańska 14', 'Floriańska 14'),
        ('ul Floriańska 14', 'Floriańska 14'),
        ('ulica Floriańska', 'Floriańska'),
        ('Kraków, ul. Floriańska 14', 'Kraków, Floriańska 14'),
    ])
    def test_ul_is_dropped(self, q, required):
        assert read_query(q).required == required

    @pytest.mark.parametrize('q', ['Ulmowa 3', 'Ułańska 2', 'Pulawska 1', 'Kraków, Ulanów'])
    def test_only_as_a_word_of_its_own(self, q):
        assert read_query(q).required == q

    @pytest.mark.parametrize('q,relaxed,scored', [
        ('al. 3 Maja 5', '3 Maja 5', 'aleja 3 Maja 5'),
        ('Al. Jerozolimskie 65', 'Jerozolimskie 65', 'aleja Jerozolimskie 65'),
        ('os. Tysiąclecia 12', 'Tysiąclecia 12', 'osiedle Tysiąclecia 12'),
        ('pl. Defilad 1', 'Defilad 1', 'plac Defilad 1'),
    ])
    def test_al_os_pl_are_required_until_nothing_has_them(self, q, relaxed, scored):
        """As typed they are the start of the word, so Aleja and Aleje are both found."""
        reading = read_query(q)
        assert (reading.required, reading.relaxed, reading.scored) == (q, relaxed, scored)

    def test_a_postcode_is_not_a_word(self):
        reading = read_query('Floriańska 14, 31-019 Kraków')
        assert reading.postcodes == ['31-019']
        assert reading.required == 'Floriańska 14, Kraków'
        assert reading.numbers == ['14']

    @pytest.mark.parametrize('q', ['Marszałkowska 29-30', 'Wiejska 4/6/8', '00700 Roma'])
    def test_nor_is_anything_else_a_postcode(self, q):
        assert read_query(q).postcodes == []

    @pytest.mark.parametrize('q,postcodes,required,numbers', [
        ('Sierichstraße 183, 22299 Hamburg', ['22299'], 'Sierichstraße 183, Hamburg', ['183']),
        ('22299 Hamburg, Sierichstraße 183', ['22299'], 'Hamburg, Sierichstraße 183', ['183']),
        ('Via Roma 5, 00184 Roma', ['00184'], 'Via Roma 5, Roma', ['5']),
        # every number has five digits: the last, as an American address ends
        ('12345 Main Street, Springfield 62701', ['62701'], '12345 Main Street, Springfield',
         ['12345']),
    ])
    def test_five_digits_beside_a_house_number_are_a_postcode(self, q, postcodes, required, numbers):
        """Read as a second house number, nothing had every word."""
        reading = read_query(q)
        assert (reading.postcodes, reading.required, reading.numbers) == (postcodes, required, numbers)

    @pytest.mark.parametrize('q', ['12345 Main Street', '22299 Hamburg', 'Hauptstraße 12345'])
    def test_alone_five_digits_are_a_house_number(self, q):
        reading = read_query(q)
        assert reading.postcodes == [] and reading.required == q

    @pytest.mark.parametrize('q', ['Floriańska 14 m. 3', 'Floriańska 14 m 3', 'Floriańska 14 M.3',
                                   'Floriańska 14 lok. 3', 'Floriańska 14 lokal 3'])
    def test_a_flat_after_the_house_is_dropped(self, q):
        reading = read_query(q)
        assert (reading.required, reading.scored, reading.numbers) == (
            'Floriańska 14', 'Floriańska 14', ['14'])

    @pytest.mark.parametrize('q', ['Lipowa 3 Maja 5', 'Dolne 1 Maja 2', 'Lipowa 3 Mielec'])
    def test_a_word_after_a_number_is_no_flat(self, q):
        assert read_query(q).required == q

    @pytest.mark.parametrize('q,relaxed', [
        ('Osiedle Urocze 1 Kraków', 'Urocze 1 Kraków'),
        ('Aleja 3 Maja 5', '3 Maja 5'),
        ('Kraków, Plac Nowy', 'Kraków, Nowy'),
    ])
    def test_and_so_are_osiedle_aleja_plac_typed_whole(self, q, relaxed):
        """A name leaves them out as often as it carries them, however they are typed."""
        reading = read_query(q)
        assert (reading.required, reading.relaxed, reading.scored) == (q, relaxed, q)

    @pytest.mark.parametrize('q', ['Osiedle', 'Plac', 'aleja', 'os. 5', 'Floriańska 14', 'Abbey Rd'])
    def test_nothing_to_relax(self, q):
        """Alone, or with only a number, the word is the query; other words are not left out."""
        assert read_query(q).relaxed is None

    def test_relaxed_the_words_a_name_leaves_out_are_not_required(self):
        strict = build_query(q='Osiedle Urocze 1')['bool']['must'][0]['multi_match']
        relaxed = build_query(q='Osiedle Urocze 1', relaxed=True)['bool']
        assert strict['query'] == 'Osiedle Urocze 1'
        assert relaxed['must'][0]['multi_match']['query'] == 'Urocze 1'
        # and it still scores
        assert relaxed['should'][0]['multi_match']['query'] == 'Osiedle Urocze 1'

    @pytest.mark.parametrize('q,required', [
        ('Floriańska 14/3, Kraków', 'Floriańska 14, Kraków'),
        ('Floriańska 12a/3', 'Floriańska 12a'),
        ('Lipowa 1 Maja 31/33', 'Lipowa 1 Maja 31'),
        ('3/5 Maja', '3 Maja'),
    ])
    def test_after_a_slash_it_scores_and_is_not_required(self, q, required):
        """14/3 is as often house 14, flat 3, as a house numbered 14/3."""
        reading = read_query(q)
        assert reading.required == required
        assert reading.scored == q

    @pytest.mark.parametrize('q', ['Via Girolamo Cocconi 15/a, Roma', 'Sobolowa 2/LU4, Łódź',
                                   'Via Roma 15/A'])
    def test_a_letter_after_the_slash_is_the_house_not_a_flat(self, q):
        """In Italy 15/a is a house of its own, beside 15."""
        assert read_query(q).required == q

    def test_the_number_is_kept_whole(self):
        assert read_query('Floriańska 14/3').numbers == ['14/3']

    @pytest.mark.parametrize('q', ['ul.', '31-019', 'ul. 31-019'])
    def test_nothing_left_is_the_query_as_typed(self, q):
        assert read_query(q).required == q

    def test_everything_at_once(self):
        reading = read_query('ul. Floriańska 14/3 m. 2, 31-019 Kraków')
        assert reading.required == 'Floriańska 14, Kraków'
        assert reading.postcodes == ['31-019']


class TestStreetWordsOfOtherLanguages:
    """Str., Rd, Ave, bd, v.le: written out for scoring, never required."""

    @pytest.mark.parametrize('q,required,scored', [
        # the start of its word: found as a prefix, so it stays required
        ('Berliner Str. 5', 'Berliner Str. 5', 'Berliner straße 5'),
        ('Berliner Str 5 Potsdam', 'Berliner Str 5 Potsdam', 'Berliner straße 5 Potsdam'),
        ('Fifth Ave', 'Fifth Ave', 'Fifth avenue'),
        # not the start of its word: "rd" finds no Road
        ('Abbey Rd', 'Abbey', 'Abbey road'),
        ('Sunset Blvd.', 'Sunset', 'Sunset boulevard'),
        ('bd Haussmann', 'Haussmann', 'boulevard Haussmann'),
        ('V.le Europa 3', 'Europa 3', 'viale Europa 3'),
        ('P.zza Navona', 'Navona', 'piazza Navona'),
        ('Avda. de América', 'de América', 'avenida de América'),
    ])
    def test_written_out(self, q, required, scored):
        reading = read_query(q)
        assert (reading.required, reading.scored) == (required, scored)

    @pytest.mark.parametrize('q,scored', [
        ('Hauptstr. 5', 'Hauptstraße 5'),
        ('Hauptstr 5 Bonn', 'Hauptstraße 5 Bonn'),
        ('Bahnhofstr.', 'Bahnhofstraße'),
    ])
    def test_glued_to_the_name_it_is_written_out_and_still_required(self, q, scored):
        reading = read_query(q)
        assert reading.scored == scored
        assert reading.required == q

    def test_an_abbreviation_of_several_words_is_tried_as_each(self):
        """The query does not say its country: pl. is plac, Platz and Place."""
        reading = read_query('Pariser Pl. 1')
        assert reading.variants == ('Pariser plac 1', 'Pariser platz 1', 'Pariser place 1')
        assert reading.also == ('platz', 'place')
        assert (reading.required, reading.relaxed) == ('Pariser Pl. 1', 'Pariser 1')

    def test_each_reading_is_tried_as_a_whole_name(self):
        from django_osm_locations.api.queries import exact_name_functions

        tried = [list(clause['match'].values())[0]
                 for clause in exact_name_functions('Pariser Pl. Berlin')[0]['filter']['bool']['should']]
        assert 'Pariser platz' in tried and 'Pariser plac' in tried

    def test_without_a_dot_it_may_be_a_name_being_typed(self):
        """"Kraków Rd" is on its way to Rdzawa as likely as it is a road."""
        reading = read_query('Kraków Rd')
        assert reading.required == 'Kraków'
        assert reading.also == ('Rd',)
        scored = build_query(q='Kraków Rd')['bool']['should'][0]['multi_match']['query']
        assert scored == 'Kraków road Rd'

    @pytest.mark.parametrize('q', ['Rd', 'Al', 'Os', 'Tr', 'Alabama', 'Trakt 5', 'Ostrów al'])
    def test_what_is_not_an_abbreviation(self, q):
        """Alone, or without the dot the language writes: the start of a name."""
        reading = read_query(q)
        assert (reading.required, reading.scored) == (q, q)

    def test_st_is_left_as_typed(self):
        """Street, saint, sankt and sint: and already the start of three of them."""
        reading = read_query('St. Pauli 3')
        assert (reading.required, reading.scored) == ('St. Pauli 3', 'St. Pauli 3')


class TestTypos:
    def test_every_word_as_typed_or_misspelt(self):
        from django_osm_locations.api.queries import FUZZY_BOOST

        either = build_query(q='Marszałkowska Warszwa', fuzzy=True)['bool']['must'][0]['bool']
        as_typed, misspelt = either['should']
        assert either['minimum_should_match'] == 1
        assert as_typed == build_query(q='Marszałkowska Warszwa')
        assert misspelt['bool']['boost'] == FUZZY_BOOST
        words = [clause['bool']['should'][1]['multi_match'] for clause in misspelt['bool']['must']]
        assert [w['query'] for w in words] == ['Marszałkowska', 'Warszwa']
        assert all(w['fuzziness'] == 'AUTO' and w['prefix_length'] == 1 for w in words)

    def test_a_word_may_also_be_the_start_of_one(self):
        """The last word is often still being typed: "Warszwa Marsz"."""
        misspelt = build_query(q='Warszwa Marsz', fuzzy=True)['bool']['must'][0]['bool']['should'][1]
        prefix = misspelt['bool']['must'][1]['bool']['should'][0]['multi_match']
        assert prefix['query'] == 'Marsz' and 'fuzziness' not in prefix

    def test_no_address_is_found_misspelt(self):
        misspelt = build_query(q='Warszwa', fuzzy=True)['bool']['must'][0]['bool']['should'][1]
        assert misspelt['bool']['must_not'] == [{'term': {'type': 'addresses'}}]

    def test_a_house_number_is_not_a_word_to_misspell(self):
        from django_osm_locations.api.queries import fuzzy_words

        assert fuzzy_words('ul. Marszałkowska 10, Warszwa') == ['Marszałkowska', 'Warszwa']
        assert fuzzy_words('12') == []

    @pytest.mark.parametrize('q,name,ancestry,found', [
        ('Marszałkowska Warszawa', 'Marszałkowska', ['Polska', 'Warszawa'], True),
        ('marszalkowska warszawa', 'Marszałkowska', ['Polska', 'Warszawa'], True),
        ('Marszałkowska Warsz', 'Marszałkowska', ['Polska', 'Warszawa'], True),
        ('Marszałkowska 10 Warszawa', 'Marszałkowska', ['Polska', 'Warszawa'], True),
        ('Szczecno', 'Szczecno', ['Polska'], True),
        # one word, and only the start of the name: how a typo finds a village
        ('Szczecn', 'Szczecno', ['Polska'], False),
        ('Hambrug', 'Hambrüggeweg', ['Deutschland', 'Metelen'], False),
        ('Marszałk Warszawa', 'Marszałkowska', ['Polska', 'Warszawa'], False),
    ])
    def test_found_as_typed(self, q, name, ancestry, found):
        from django_osm_locations.api.queries import found_as_typed

        source = {'name': name, 'ancestry': [{'name': a} for a in ancestry]}
        assert found_as_typed(q, source) is found

    def test_a_translation_counts(self):
        from django_osm_locations.api.queries import found_as_typed

        assert found_as_typed('Warsaw', {'name': 'Warszawa', 'names': {'en': 'Warsaw'}})


class TestAddressesTooEarly:
    @pytest.mark.parametrize('q', ['M', 'Ma', 'Mü', 'k.'])
    def test_one_or_two_letters_mean_no_address_yet(self, q):
        from django_osm_locations.api.queries import without_addresses_too_early

        assert without_addresses_too_early(['places', 'streets', 'addresses'], q) == [
            'places', 'streets']

    @pytest.mark.parametrize('q', ['Mar', 'M 1', '12', None, ''])
    def test_three_letters_or_a_digit_do(self, q):
        from django_osm_locations.api.queries import without_addresses_too_early

        names = ['places', 'streets', 'addresses']
        assert without_addresses_too_early(names, q) == names

    def test_addresses_asked_for_alone_are_searched(self):
        from django_osm_locations.api.queries import without_addresses_too_early

        assert without_addresses_too_early(['addresses'], 'Ma') == ['addresses']


class TestTimeout:
    def test_a_search_is_given_two_seconds(self):
        assert build_body({'q': 'krak'})[0]['timeout'] == '2s'

    def test_or_what_the_settings_say(self, settings):
        settings.LOCATIONS_SEARCH_TIMEOUT = '500ms'
        assert build_body({'q': 'krak'})[0]['timeout'] == '500ms'

    def test_or_no_limit(self, settings):
        settings.LOCATIONS_SEARCH_TIMEOUT = None
        assert 'timeout' not in build_body({'q': 'krak'})[0]


class TestBbox:
    def test_builds_a_bounding_box(self):
        clause = parse_bbox('-5,54,-4,55')
        box = clause['geo_bounding_box']['center']
        assert box['top_left'] == {'lon': -5.0, 'lat': 55.0}
        assert box['bottom_right'] == {'lon': -4.0, 'lat': 54.0}

    def test_none_when_absent(self):
        assert parse_bbox(None) is None

    @pytest.mark.parametrize('value', ['1,2,3', '1,2,3,4,5', 'a,b,c,d'])
    def test_rejects_malformed(self, value):
        with pytest.raises(QueryError):
            parse_bbox(value)


class TestNear:
    def test_builds_a_distance_clause(self):
        clause = parse_near('-4.48,54.15', '5km')
        assert clause['geo_distance']['distance'] == '5km'
        assert clause['geo_distance']['center'] == {'lon': -4.48, 'lat': 54.15}

    def test_radius_defaults(self):
        assert parse_near('0,0', None)['geo_distance']['distance'] == '10km'

    def test_rejects_malformed(self):
        with pytest.raises(QueryError):
            parse_near('1', '5km')


class TestPaging:
    def test_defaults(self):
        assert parse_page(None, None) == (1, 20)

    def test_caps_page_size(self):
        assert parse_page(1, 10_000)[1] == 200

    @pytest.mark.parametrize('page,size', [('x', 10), (1, 'x'), (1, 0)])
    def test_rejects_junk(self, page, size):
        with pytest.raises(QueryError):
            parse_page(page, size)


class TestQuery:
    def test_match_all_without_parameters(self):
        assert build_query() == {'match_all': {}}

    def test_text_goes_to_must_so_it_scores(self):
        query = build_query(q='krakow')
        assert 'must' in query['bool']
        assert 'filter' not in query['bool']

    def test_within_is_a_filter_not_a_scorer(self):
        query = build_query(within='douglas')
        assert query['bool']['filter'] == [{'term': {'ancestry_slugs': 'douglas'}}]
        assert 'must' not in query['bool']

    def test_within_accepts_several_ancestors(self):
        filters = build_query(within='douglas,braddan')['bool']['filter']
        assert {'term': {'ancestry_slugs': 'douglas'}} in filters
        assert {'term': {'ancestry_slugs': 'braddan'}} in filters

    def test_within_an_ancestor_of_one_type(self):
        """Antwerpen is a city and the province around it, both antwerpen."""
        assert parse_within('places:antwerpen') == [{'nested': {'path': 'ancestry', 'query': {
            'bool': {'filter': [{'term': {'ancestry.level': 'place'}},
                                {'term': {'ancestry.slug': 'antwerpen'}}]}}}}]

    def test_within_of_both_kinds(self):
        filters = build_query(within='belgie, place-subareas:deurne')['bool']['filter']
        assert filters[0] == {'term': {'ancestry_slugs': 'belgie'}}
        assert filters[1]['nested']['query']['bool']['filter'][0] == {
            'term': {'ancestry.level': 'placesubarea'}}

    @pytest.mark.parametrize('value', ['addresses:lakowa-5', 'cities:antwerpen', ':antwerpen'])
    def test_within_a_type_no_ancestor_has(self, value):
        with pytest.raises(QueryError):
            parse_within(value)

    def test_text_and_filters_combine(self):
        query = build_query(q='abbey', within='douglas', bbox='-5,54,-4,55')
        assert len(query['bool']['must']) == 1
        assert len(query['bool']['filter']) == 2

    def test_name_is_weighted_above_ancestry(self):
        fields = build_query(q='x')['bool']['should'][0]['multi_match']['fields']
        assert fields[0].startswith('name.exact')
        assert 'ancestry_names' in fields

    def test_every_word_has_to_be_found(self):
        """
        "krakow, kazimierz": the Kazimierz in Kraków has both words, one in its
        name and one in its ancestry, and came 337th when either would do.
        """
        match = build_query(q='krakow, kazimierz')['bool']['must'][0]['multi_match']
        assert match['type'] == 'cross_fields'
        assert match['operator'] == 'and'
        assert set(match['fields']) == {'name', 'ancestry_names', 'names_all'}

    def test_relaxed_any_word_will_do_as_before(self):
        query = build_query(q='krakow, kazimierz', strict=False)
        assert 'should' not in query['bool']
        match = query['bool']['must'][0]['multi_match']
        assert match['type'] == 'best_fields'
        assert 'operator' not in match

    def test_a_word_found_whole_scores_above_a_prefix(self):
        """"Lublin" found whole, not only as the start of Lubliniec."""
        words = build_query(q='Elizy Orzeszkowej 1, Lublin')['bool']['should'][1]['multi_match']
        assert set(words['fields']) == {'name.words^2', 'place_names^2', 'ancestry_names.words',
                                        'names_all.words^0.5'}
        assert words['type'] == 'cross_fields'
        assert 'operator' not in words

    def test_found_whole_it_counts_for_more_than_once(self):
        """
        A city typed whole as the start of another street's name counts in
        two clauses: at 1.0 "Ulica 4 Karlovac" came out as Karlovačka 4 in
        Pula. Measured on nine packs, 1.25 and 1.5 changed nothing else.
        """
        from django_osm_locations.api import queries

        words = build_query(q='Ulica 4 Karlovac')['bool']['should'][1]['multi_match']
        assert words['boost'] == queries.WHOLE_WORD_BOOST
        assert 1.25 <= queries.WHOLE_WORD_BOOST <= 1.5

    def test_and_a_whole_translation_as_a_whole_name(self):
        fields = build_query(q='Warsaw')['bool']['should'][0]['multi_match']['fields']
        assert fields.index('names_all.exact^3') == 1

    def test_runs_of_words(self):
        assert word_runs('Berlin, Friedrichshain') == [
            'Berlin', 'Berlin Friedrichshain', 'Friedrichshain']

    def test_a_number_alone_is_no_name(self):
        assert word_runs('Floriańska 14/3') == ['Floriańska', 'Floriańska 14/3']

    def test_runs_are_not_longer_than_that(self):
        runs = word_runs('a b c d e f g h')
        assert 'a b c d e f' in runs and 'a b c d e f g' not in runs

    def test_relaxed_has_no_whole_words_to_add(self):
        """Anything with any word scores already; whole ones are for telling apart."""
        query = build_query(q='Elizy Orzeszkowej 1, Lublin', strict=False)
        assert len(query['bool']['must']) == 1 and 'should' not in query['bool']

    def test_what_is_required_and_what_scores(self):
        query = build_query(q='ul. al. Daszyńskiego 14/3, Kraków')['bool']
        assert query['must'][0]['multi_match']['query'] == 'al. Daszyńskiego 14, Kraków'
        assert query['should'][0]['multi_match']['query'] == 'aleja Daszyńskiego 14/3, Kraków'
        without = build_query(q='ul. al. Daszyńskiego 14/3, Kraków', relaxed=True)['bool']
        assert without['must'][0]['multi_match']['query'] == 'Daszyńskiego 14, Kraków'

    def test_relaxed_any_word_of_what_is_required(self):
        """A flat number would find every house 3 in the country."""
        query = build_query(q='Floriańska 14 m. 3', strict=False)
        assert query['bool']['must'][0]['multi_match']['query'] == 'Floriańska 14'


class TestTwins:
    """Deurne, a point, and Deurne, the district of Antwerpen around it."""

    def test_search_leaves_the_point_out(self):
        body, _, _ = build_body({'q': 'Antwerpen, Deurne'})
        must_not = body['query']['function_score']['query']['bool']['must_not']
        assert {'exists': {'field': 'twin_of'}} in must_not

    def test_unless_the_districts_are_not_searched(self):
        body, _, _ = build_body({'q': 'Deurne'}, types=['places'])
        assert 'must_not' not in body['query']['function_score']['query']['bool']

    def test_a_listing_has_both(self):
        body, _, _ = build_body({'within': 'antwerpen'})
        assert 'must_not' not in body['query']['bool']


class TestSort:
    def test_relevance_by_default_with_a_query(self):
        assert build_sort(None, has_query=True) == ['_score']

    def test_size_by_default_without_a_query(self):
        """Relevance is meaningless with no query, so biggest first."""
        assert build_sort(None, has_query=False)[0]['area']['order'] == 'desc'

    def test_by_name(self):
        assert build_sort('name', True)[0]['name.keyword']['order'] == 'asc'

    @pytest.mark.parametrize('order', [None, 'size', 'name'])
    def test_sorts_tolerate_indexes_without_the_field(self, order):
        """
        A cross-type search spans indexes with different mappings -- places have
        area, streets have length -- and sorting on a field one of them does not
        define is a 400 from OpenSearch without unmapped_type.
        """
        for clause in build_sort(order, has_query=False):
            if isinstance(clause, dict):
                for options in clause.values():
                    assert 'unmapped_type' in options


class TestBody:
    def test_paging_becomes_from_and_size(self):
        body, page, page_size = build_body({'page': '3', 'page_size': '10'})
        assert (body['from'], body['size']) == (20, 10)
        assert (page, page_size) == (3, 10)

    def test_geometry_excluded_by_default(self):
        body, _, _ = build_body({})
        assert body['_source']['excludes'] == ['geometry']

    def test_geometry_included_on_request(self):
        body, _, _ = build_body({'geometry': 'true'})
        assert '_source' not in body

    def test_total_hits_are_tracked(self):
        body, _, _ = build_body({})
        assert body['track_total_hits'] is True

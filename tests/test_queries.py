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
    read_query,
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

    @pytest.mark.parametrize('q,required,scored', [
        ('al. 3 Maja 5', '3 Maja 5', 'aleja 3 Maja 5'),
        ('Al. Jerozolimskie 65', 'Jerozolimskie 65', 'aleja Jerozolimskie 65'),
        ('os. Tysiąclecia 12', 'Tysiąclecia 12', 'osiedle Tysiąclecia 12'),
        ('pl. Defilad 1', 'Defilad 1', 'plac Defilad 1'),
    ])
    def test_al_os_pl_are_not_required_but_score(self, q, required, scored):
        reading = read_query(q)
        assert (reading.required, reading.scored) == (required, scored)

    def test_a_postcode_is_not_a_word(self):
        reading = read_query('Floriańska 14, 31-019 Kraków')
        assert reading.postcodes == ['31-019']
        assert reading.required == 'Floriańska 14, Kraków'
        assert reading.numbers == ['14']

    @pytest.mark.parametrize('q', ['Marszałkowska 29-30', 'Wiejska 4/6/8', '00700 Roma'])
    def test_nor_is_anything_else_a_postcode(self, q):
        assert read_query(q).postcodes == []

    @pytest.mark.parametrize('q', ['Floriańska 14 m. 3', 'Floriańska 14 m 3', 'Floriańska 14 M.3',
                                   'Floriańska 14 lok. 3', 'Floriańska 14 lokal 3'])
    def test_a_flat_after_the_house_is_dropped(self, q):
        reading = read_query(q)
        assert (reading.required, reading.scored, reading.numbers) == (
            'Floriańska 14', 'Floriańska 14', ['14'])

    @pytest.mark.parametrize('q', ['Aleja 3 Maja 5', 'Osiedle 1 Maja 2', 'Lipowa 3 Mielec'])
    def test_a_word_after_a_number_is_no_flat(self, q):
        assert read_query(q).required == q

    @pytest.mark.parametrize('q,required', [
        ('Floriańska 14/3, Kraków', 'Floriańska 14, Kraków'),
        ('Floriańska 12a/3', 'Floriańska 12a'),
        ('Aleja 1 Maja 31/33', 'Aleja 1 Maja 31'),
        ('3/5 Maja', '3 Maja'),
    ])
    def test_after_a_slash_it_scores_and_is_not_required(self, q, required):
        """14/3 is as often house 14, flat 3, as a house numbered 14/3."""
        reading = read_query(q)
        assert reading.required == required
        assert reading.scored == q

    def test_the_number_is_kept_whole(self):
        assert read_query('Floriańska 14/3').numbers == ['14/3']

    @pytest.mark.parametrize('q', ['ul.', '31-019', 'ul. 31-019'])
    def test_nothing_left_is_the_query_as_typed(self, q):
        assert read_query(q).required == q

    def test_everything_at_once(self):
        reading = read_query('ul. Floriańska 14/3 m. 2, 31-019 Kraków')
        assert reading.required == 'Floriańska 14, Kraków'
        assert reading.postcodes == ['31-019']


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
        assert set(words['fields']) == {'name.words^2', 'ancestry_names.words',
                                        'names_all.words^0.5'}
        assert words['type'] == 'cross_fields'
        assert 'operator' not in words

    def test_and_a_whole_translation_as_a_whole_name(self):
        fields = build_query(q='Warsaw')['bool']['should'][0]['multi_match']['fields']
        assert fields.index('names_all.exact^3') == 1

    def test_relaxed_has_no_whole_words_to_add(self):
        """Anything with any word scores already; whole ones are for telling apart."""
        query = build_query(q='Elizy Orzeszkowej 1, Lublin', strict=False)
        assert len(query['bool']['must']) == 1 and 'should' not in query['bool']

    def test_what_is_required_and_what_scores(self):
        query = build_query(q='ul. al. Daszyńskiego 14/3, Kraków')['bool']
        assert query['must'][0]['multi_match']['query'] == 'Daszyńskiego 14, Kraków'
        assert query['should'][0]['multi_match']['query'] == 'aleja Daszyńskiego 14/3, Kraków'

    def test_relaxed_any_word_of_what_is_required(self):
        """A flat number would find every house 3 in the country."""
        query = build_query(q='Floriańska 14 m. 3', strict=False)
        assert query['bool']['must'][0]['multi_match']['query'] == 'Floriańska 14'


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

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
    parse_bbox,
    parse_near,
    parse_page,
    parse_types,
)


class TestTypes:
    def test_defaults_to_every_type(self):
        assert set(parse_types(None)) == {
            'countries', 'country-subareas', 'places', 'place-subareas', 'streets',
        }

    def test_parses_a_subset(self):
        assert parse_types('places,streets') == ['places', 'streets']

    def test_rejects_unknown_types(self):
        with pytest.raises(QueryError) as exc:
            parse_types('places,villages')
        assert 'villages' in str(exc.value)


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
        fields = build_query(q='x')['bool']['must'][0]['multi_match']['fields']
        assert fields[0].startswith('name.exact')
        assert 'ancestry_names' in fields


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

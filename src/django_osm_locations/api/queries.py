"""
Translating request parameters into OpenSearch queries.

Kept apart from the views so the query shape can be tested without a server.
"""
from django.conf import settings

from ..search.indexes import INDEXES

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 20

# Geometry is in the index, but sending a country border with every hit in a
# list response is wasteful, so it is excluded unless explicitly asked for.
LIST_EXCLUDES = ['geometry']


class QueryError(ValueError):
    """A request parameter that cannot be turned into a query."""


def parse_types(value):
    """``?type=places,streets`` -> index names, defaulting to everything."""
    if not value:
        return list(INDEXES)
    names = [part.strip() for part in value.split(',') if part.strip()]
    unknown = [name for name in names if name not in INDEXES]
    if unknown:
        raise QueryError(
            'Unknown type(s): {}. Known types: {}'.format(
                ', '.join(unknown), ', '.join(sorted(INDEXES))
            )
        )
    return names


def parse_bbox(value):
    """``?bbox=minlon,minlat,maxlon,maxlat`` -> a geo_bounding_box clause."""
    if not value:
        return None
    parts = [part.strip() for part in value.split(',')]
    if len(parts) != 4:
        raise QueryError('bbox needs 4 comma-separated numbers: minlon,minlat,maxlon,maxlat')
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in parts)
    except ValueError:
        raise QueryError('bbox values must be numbers')
    return {
        'geo_bounding_box': {
            'center': {
                'top_left': {'lon': min_lon, 'lat': max_lat},
                'bottom_right': {'lon': max_lon, 'lat': min_lat},
            }
        }
    }


def parse_near(value, radius):
    """``?near=lon,lat&radius=5km`` -> a geo_distance clause."""
    if not value:
        return None
    parts = [part.strip() for part in value.split(',')]
    if len(parts) != 2:
        raise QueryError('near needs 2 comma-separated numbers: lon,lat')
    try:
        lon, lat = (float(part) for part in parts)
    except ValueError:
        raise QueryError('near values must be numbers')
    return {
        'geo_distance': {
            'distance': radius or '10km',
            'center': {'lon': lon, 'lat': lat},
        }
    }


def parse_page(page, page_size):
    """
    ``?page=`` / ``?page_size=`` -> offset and size.

    Absent means "use the default"; present but zero or negative is a mistake
    worth reporting rather than silently turning into the default.
    """
    if page in (None, ''):
        page = 1
    else:
        try:
            page = int(page)
        except (TypeError, ValueError):
            raise QueryError('page must be a positive integer')
        if page < 1:
            raise QueryError('page must be a positive integer')

    if page_size in (None, ''):
        page_size = DEFAULT_PAGE_SIZE
    else:
        try:
            page_size = int(page_size)
        except (TypeError, ValueError):
            raise QueryError('page_size must be a positive integer')
        if page_size < 1:
            raise QueryError('page_size must be a positive integer')

    return page, min(page_size, MAX_PAGE_SIZE)


def parse_admin_level(value):
    """``?admin_level=4`` or ``?admin_level=4,6`` -> a terms filter."""
    if not value:
        return None
    try:
        levels = [int(part) for part in str(value).split(',') if part.strip()]
    except ValueError:
        raise QueryError('admin_level must be a number, or several separated by commas')
    if not levels:
        return None
    return {'terms': {'admin_level': levels}}


def build_query(q=None, within=None, bbox=None, near=None, radius=None,
                admin_level=None):
    """
    Assemble the bool query.

    ``q`` scores; everything else filters, so filters stay out of the scoring
    path and get cached by OpenSearch.
    """
    must, filters = [], []

    if q:
        must.append({
            'multi_match': {
                'query': q,
                # the name matters most, its unfolded form more than the
                # n-grammed one, and ancestry lets "krakow florianska" work
                'fields': ['name.exact^4', 'name^2', 'ancestry_names'],
                'type': 'best_fields',
            }
        })

    if within:
        for slug in [part.strip() for part in within.split(',') if part.strip()]:
            filters.append({'term': {'ancestry_slugs': slug}})

    bbox_clause = parse_bbox(bbox)
    if bbox_clause:
        filters.append(bbox_clause)

    near_clause = parse_near(near, radius)
    if near_clause:
        filters.append(near_clause)

    level_clause = parse_admin_level(admin_level)
    if level_clause:
        filters.append(level_clause)

    if not must and not filters:
        return {'match_all': {}}

    query = {'bool': {}}
    if must:
        query['bool']['must'] = must
    if filters:
        query['bool']['filter'] = filters
    return query


# A search can span indexes with different mappings -- places have `area`,
# streets have `length` -- and sorting on a field an index does not define is an
# error unless `unmapped_type` says how to treat it.
SIZE_SORT = [
    {'area': {'order': 'desc', 'missing': '_last', 'unmapped_type': 'double'}},
    {'length': {'order': 'desc', 'missing': '_last', 'unmapped_type': 'double'}},
]


# How much each type is worth relative to the others on an equal text match.
# Without this, searching "warszawa" over a country's worth of data returns the
# three districts named Warszawa before the city of 1.8 million people, because
# nothing in a text score knows that a place matters more than a subdivision of
# one. Override with LOCATIONS_TYPE_BOOSTS.
DEFAULT_TYPE_BOOSTS = {
    'countries': 8.0,
    # A place outranks the administrative area sharing its name: Poland has a
    # city of Gdańsk and a county of Gdańsk, and someone typing "gdansk" means
    # the city.
    'places': 5.0,
    'country-subareas': 4.0,
    'place-subareas': 2.0,
    'streets': 1.0,
}


def type_boosts():
    configured = getattr(settings, 'LOCATIONS_TYPE_BOOSTS', None)
    return dict(configured) if configured else dict(DEFAULT_TYPE_BOOSTS)


def apply_scoring(query):
    """
    Scale each hit's score by what kind of location it is, and by its size.

    Both are multiplicative rather than additive: an additive boost competes
    with the text score and its effect depends on how well the text matched,
    while a weight multiplies whatever the text scored, so the ordering between
    types holds for a strong match and a weak one alike.

    The size factor is what separates hits that match the text equally well. A
    prefix search for "krak" matches Kraków, Krakowiany and Krakuszowice
    identically -- nothing in the text says which one anybody meant -- so the
    city of 800,000 should come first. Population is sparse in OSM, hence the
    fallback to area, and both are damped by a logarithm so that size nudges
    the order rather than dictating it.
    """
    functions = [
        {'filter': {'term': {'type': name}}, 'weight': weight}
        for name, weight in sorted(type_boosts().items())
    ]
    functions.append({
        'field_value_factor': {
            'field': 'population',
            'modifier': 'log2p',
            'factor': 1.0,
            'missing': 0,
        }
    })
    functions.append({
        'field_value_factor': {
            'field': 'area',
            'modifier': 'log2p',
            'factor': 1.0,
            'missing': 0,
        }
    })
    return {
        'function_score': {
            'query': query,
            'functions': functions,
            'score_mode': 'multiply',
            'boost_mode': 'multiply',
        }
    }


def build_sort(order, has_query):
    """
    ``?order=`` -> a sort clause.

    Relevance is the default when there is a query. Without one it would be
    meaningless, so size takes over: largest area, longest street.
    """
    if order == 'name':
        return [{'name.keyword': {'order': 'asc', 'unmapped_type': 'keyword'}}]
    if order == 'size':
        return list(SIZE_SORT)
    if order == 'relevance' or (order is None and has_query):
        return ['_score']
    return list(SIZE_SORT)


def build_body(params):
    """Turn a request's query parameters into a full search body."""
    q = params.get('q') or None
    page, page_size = parse_page(params.get('page'), params.get('page_size'))
    include_geometry = str(params.get('geometry', '')).lower() in ('1', 'true', 'yes')

    query = build_query(
        q=q,
        within=params.get('within'),
        bbox=params.get('bbox'),
        near=params.get('near'),
        radius=params.get('radius'),
        admin_level=params.get('admin_level'),
    )
    if q:
        # only meaningful when something is being scored; without a query the
        # results are sorted by size anyway
        query = apply_scoring(query)

    body = {
        'query': query,
        'sort': build_sort(params.get('order'), bool(q)),
        'from': (page - 1) * page_size,
        'size': page_size,
        'track_total_hits': True,
    }
    if not include_geometry:
        body['_source'] = {'excludes': LIST_EXCLUDES}
    return body, page, page_size

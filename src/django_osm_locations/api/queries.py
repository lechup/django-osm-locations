"""
Translating request parameters into OpenSearch queries.

Kept apart from the views so the query shape can be tested without a server.
"""
import re
from collections import Counter, namedtuple

from django.conf import settings

from ..search.indexes import INDEXES
from ..utils.twins import TWIN_OF

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 20

# Geometry is in the index, but sending a country border with every hit in a
# list response is wasteful, so it is excluded unless explicitly asked for.
LIST_EXCLUDES = ['geometry']


class QueryError(ValueError):
    """A request parameter that cannot be turned into a query."""


def _type_names(value):
    names = [part.strip() for part in value.split(',') if part.strip()]
    unknown = [name for name in names if name not in INDEXES]
    if unknown:
        raise QueryError(
            'Unknown type(s): {}. Known types: {}'.format(
                ', '.join(unknown), ', '.join(sorted(INDEXES))
            )
        )
    return names


def parse_types(value):
    """``?type=places,streets`` -> index names, defaulting to everything."""
    if not value:
        return list(INDEXES)
    return _type_names(value)


def exclude_types(names, value):
    """
    ``?exclude_type=addresses`` -> ``names`` without those.

    For suggestions as a map offers them: streets and places while a name is
    being typed, and the client asks for addresses once there is a number.
    Listing the five other types does the same, and has to be kept up to date.
    """
    if not value:
        return list(names)
    excluded = set(_type_names(value))
    kept = [name for name in names if name not in excluded]
    if not kept:
        raise QueryError('exclude_type leaves nothing to search')
    return kept


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


#: A type, as the API names it, and the level it has in an ancestry.
ANCESTRY_LEVELS = {
    'countries': 'country',
    'country-subareas': 'countrysubarea',
    'places': 'place',
    'place-subareas': 'placesubarea',
    'streets': 'street',
}


def parse_within(value):
    """
    ``?within=douglas,places:antwerpen`` -> one filter for each ancestor.

    A slug is unique per type, not across types: Antwerpen is a city and the
    province around it, both antwerpen, so ``within=antwerpen`` is the whole
    province. In 7 of Belgium's 10 largest cities a province or an
    arrondissement has the city's slug. ``places:antwerpen`` is the city.
    """
    clauses = []
    for part in [part.strip() for part in (value or '').split(',') if part.strip()]:
        kind, typed, slug = part.partition(':')
        if not typed:
            clauses.append({'term': {'ancestry_slugs': part}})
            continue
        if kind not in ANCESTRY_LEVELS:
            raise QueryError('within: {} is not a type an ancestor can be. Those are: {}'.format(
                kind, ', '.join(ANCESTRY_LEVELS)))
        clauses.append({'nested': {'path': 'ancestry', 'query': {'bool': {'filter': [
            {'term': {'ancestry.level': ANCESTRY_LEVELS[kind]}},
            {'term': {'ancestry.slug': slug}},
        ]}}}})
    return clauses


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


# A house number as somebody types it -- 14, 12a, 12A, 5/7, 29-30, 4bis: a run
# that starts with a digit, wherever in the query it stands. "Gajcego 14,
# Łódź", "Łódź, Gajcego 14" and "14 Gajcego" all carry one; A4 does not, being
# a road.
HOUSE_NUMBER = re.compile(r'(?<!\w)\d[\w/-]*')
HOUSE_NUMBER_LEADING_DIGITS = re.compile(r'\d+')

# What people in Poland write around an address and no name in OSM carries.
# Against the Poland pack every "ul. ..." of the API check missed, as did
# every query with a postcode, and 93 of 98 with a flat number: each was a
# word no name had, so nothing had every word, and the fallback to any word
# answered with the city.
#
# "ul." is never part of a name: OSM has Floriańska, not ul. Floriańska.
STREET_WORD = re.compile(r'(?<!\w)(?:ul\.|ulica(?!\w)|ul(?=\s))\s*', re.IGNORECASE)
# These stand for a word the name may carry -- Aleja Ignacego Daszyńskiego,
# Osiedle Tysiąclecia, Plac Defilad -- or may leave out, so they are written
# out for scoring and not required.
ABBREVIATION = re.compile(r'(?<!\w)(al|os|pl)\.\s*', re.IGNORECASE)
ABBREVIATIONS = {'al': 'aleja', 'os': 'osiedle', 'pl': 'plac'}
# 31-019, the Polish form. Five digits alone are left to be house numbers.
POSTCODE = re.compile(r'(?<!\w)\d{2}-\d{3}(?!\w)')
# The flat after the house, "14 m. 3", "14 m 3", "14 lok. 3": dropped, since
# no address goes down to the flat.
FLAT = re.compile(r'(?<!\w)(\d[\w/-]*)\s+(?:m|lok|lokal|mieszk)\.?\s*\d[\w/-]*(?!\w)', re.IGNORECASE)
# "14/3" is house 14, flat 3, as often as it is a house numbered 14/3 -- or
# 31/33, a corner of two -- so the house alone is required and the whole
# number scores. Only digits after the slash: in Italy "15/a" is the house,
# and taking it for house 15 put 15 before it.
HOUSE_PART = re.compile(r'(?<!\w)(\d+[^\W\d_]?)/\d[\w/-]*')

#: ``q`` read for what each part of the query should do with it.
#:
#: ``required`` -- the words that all have to be found
#: ``scored`` -- what ranks: the abbreviations written out, the number whole
#: ``numbers`` -- the house numbers, as typed
#: ``postcodes`` -- 31-019
QueryReading = namedtuple('QueryReading', 'required scored numbers postcodes')


def _squash(text):
    return re.sub(r'\s+,', ',', ' '.join(text.split())).strip(' ,')


def read_query(q):
    """Split ``q`` into what has to be found, what ranks, and what is not a word of any name."""
    text = q or ''
    postcodes = POSTCODE.findall(text)
    text = POSTCODE.sub(' ', text)
    text = FLAT.sub(r'\1', text)
    text = STREET_WORD.sub(' ', text)
    scored = _squash(ABBREVIATION.sub(lambda m: ABBREVIATIONS[m.group(1).lower()] + ' ', text))
    required = _squash(HOUSE_PART.sub(r'\1', ABBREVIATION.sub(' ', text)))
    if not required:
        # "ul." or a postcode alone: nothing of a name is left to require,
        # and the query as typed is a better answer than none
        required = scored = _squash(q or '')
    return QueryReading(required, scored, HOUSE_NUMBER.findall(scored), postcodes)


# What a word found whole adds, against the same word found as a prefix.
WHOLE_WORD_BOOST = 1.0
# A word found whole in the name of the place a location is in, against the
# same word in the rest of its ancestry.
PLACE_NAME_BOOST = 2
# The longest run of words tried as a whole name: see exact_name_functions.
EXACT_RUN_WORDS = 6


def whole_run(q):
    """All the words of ``q`` as one run, as word_runs writes runs."""
    return ' '.join(word for word in re.split(r'[\s,]+', read_query(q).scored) if word)


def word_runs(text, longest=EXACT_RUN_WORDS):
    """Every run of up to ``longest`` words of ``text``, commas aside, but a number alone."""
    words = [word for word in re.split(r'[\s,]+', text or '') if word]
    runs = []
    for start in range(len(words)):
        for end in range(start + 1, min(len(words), start + longest) + 1):
            run = ' '.join(words[start:end])
            if run not in runs and not HOUSE_NUMBER.fullmatch(run):
                runs.append(run)
    return runs


def build_query(q=None, within=None, bbox=None, near=None, radius=None,
                admin_level=None, strict=True):
    """
    Assemble the bool query.

    ``q`` scores; everything else filters, so filters stay out of the scoring
    path and get cached by OpenSearch.

    ``strict``: every word of ``q`` has to be found, in the name or in the
    ancestry. Matching any one of them was enough before, so "krakow,
    kazimierz" was answered with powiat krakowski, the city of Kraków and every
    Kazimierz in Poland -- and the Kazimierz in Kraków, the one that has both
    words, came 337th, since an ancestor and a name in two different fields
    earned no more than one of them. The view drops back to ``strict=False``
    when nothing has every word, so a typo still finds something.

    What is required and what scores are read from ``q`` by ``read_query``:
    "ul. Floriańska 14/3, 31-019 Kraków" requires Floriańska, 14 and Kraków.
    """
    must, should, filters = [], [], []
    reading = read_query(q) if q else None

    if q and strict:
        must.append({
            'multi_match': {
                'query': reading.required,
                # one analyzer across the three, so each word may be found in
                # whichever of them holds it
                'fields': ['name', 'ancestry_names', 'names_all'],
                'type': 'cross_fields',
                'operator': 'and',
            }
        })

    if q:
        (should if strict else must).append({
            'multi_match': {
                # relaxed, any word of what is required will do: a flat
                # number would find every house numbered 3
                'query': reading.scored if strict else reading.required,
                # the name matters most, its unfolded form more than the
                # n-grammed one, and ancestry lets "krakow florianska" work.
                # names_all sits below both so a native-name match still wins:
                # searching "polska" should not be outranked by somewhere whose
                # Polish translation happens to match. A whole translation
                # typed -- "Warsaw" -- is the native name's whole name typed.
                'fields': ['name.exact^4', 'names_all.exact^3', 'name^2', 'ancestry_names',
                           'names_all^0.5'],
                'type': 'best_fields',
            }
        })

    if q and strict:
        should.append({
            'multi_match': {
                'query': reading.required,
                # each word typed that is found whole, in whichever field;
                # see WORDS in the index definitions. A place's name above
                # the rest of the ancestry, which also has the regions named
                # after their cities: see place_names.
                'fields': ['name.words^2', 'place_names^{}'.format(PLACE_NAME_BOOST),
                           'ancestry_names.words', 'names_all.words^0.5'],
                'type': 'cross_fields',
                'boost': WHOLE_WORD_BOOST,
            }
        })

    filters.extend(parse_within(within))

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
    if should:
        query['bool']['should'] = should
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
    # Divisions rank above the settlements inside them, so that searching a
    # region's name reaches the region: "ireland" should offer Northern
    # Ireland before Belfast, and "yorkshire" the county before any town in
    # it.
    #
    # The cost is the case this used to be ordered for. Poland has a city of
    # Gdańsk and a county of Gdańsk, and someone typing "gdansk" usually means
    # the city; under these weights the county comes first. Override with
    # LOCATIONS_TYPE_BOOSTS to put it back.
    'country-subareas': 5.0,
    'places': 4.0,
    'place-subareas': 2.0,
    'streets': 1.0,
    # Below streets on purpose. There are 8.5 million addresses for Poland
    # against 1.9 million streets, so a search for "Lakowa" would otherwise
    # bury the street under every house on it.
    'addresses': 0.5,
}


def type_boosts():
    configured = getattr(settings, 'LOCATIONS_TYPE_BOOSTS', None)
    return dict(configured) if configured else dict(DEFAULT_TYPE_BOOSTS)


# house_number_sort is a 32-bit integer, and a term beyond it is an error
# rather than a miss: a phone number typed into the search box must not 400.
HOUSE_NUMBER_SORT_MAX = 2 ** 31 - 1

# What an address is worth when its number is the one typed, multiplied in as
# the type weights are. Both apply to 14 when 14 is typed, only the second to
# 14a and 14/16, and neither to 142.
#
# Together they stay below what a street is worth over an address, 2.0: the
# number decides between addresses, and does not lift one over a street that
# matched as well. At 2.0 and 1.5, "3 Maja Kraków" found Księdza Franciszka
# Maja 3 before any street called 3 Maja.
#
# A number typed twice is the exception, and the third weight is for it:
# "3 Maja 3" can only be house 3, and the street 3 Maja has every word of it.
#
# The fourth is for 14/3 typed and a house numbered 14/3 there, over house 14.
# Times the first two it is still below 2.0.
HOUSE_NUMBER_WEIGHTS = {
    'house_number': 1.25,
    'house_number_sort': 1.2,
    'typed_twice': 2.0,
    'whole_number': 1.3,
}


# What a location is worth when its whole name is some of the words typed,
# one after another. Multiplied in, as the type weights are, which an amount
# added to the text score could not outweigh: at any amount "Skopje, Butel"
# kept Opština Butel, a country subarea, before the district Butel. Measured
# on nine packs at 1, 2 and 3, the district check went 89.4, 90.6, 91.6% and
# districts found first 87.7, 90.1, 93.0%; streets and addresses held.
EXACT_NAME_WEIGHT = 3.0


# What an address is worth when it has the postcode typed. A help and not a
# requirement: 9.8% of Poland's addresses carry none, and one typed wrong
# should not lose the house. With the house number it
# may lift an address over a street, which is right -- whoever types both
# means the house.
POSTCODE_WEIGHT = 1.5


def house_number_functions(q):
    """
    Scoring functions that put the address with the typed number first.

    The number is matched as a word of the name, and names are indexed as
    prefixes, so "6" is found in Heiterwanger Straße 69 as much as in 6 and
    the two score the same. Which came first was the order they had been
    indexed in: in München 6 came sixth, behind 60-69, and in Łódź Gajcego 14
    seventh, behind 140-148 and 146A. That was 11 of the 13 address queries
    to miss the top five in the search check of the rebuilt packs.

    Matched on the keyword as typed and as upper and lower case, since OSM
    has both 12a and 12A, and on the leading integer, so a typed 14 still
    prefers 14a to 142.

    A number can be the street's rather than the house's. Every Polish town
    has a 3 Maja, an 11 Listopada, an Osiedle 1 Maja, and "3 Maja Kraków"
    asks for the street; weighting house 3 put 3 Maja 3 above it, and
    "1 Maja Łódź" got Osiedle 1 Maja 1 before the osiedle. So a number counts
    only on an address whose street does not have it already -- unless it is
    typed twice, which is "3 Maja 3": house 3 on 3 Maja, and before the
    street.

    "14/3" is matched whole and as house 14, the flat it more often means; a
    postcode or "m. 3" is not a house number at all (see ``read_query``).
    Where a house is numbered 14/3 it comes before house 14: in Estonia,
    Hungary and Moldova that is how houses are numbered, and in the repaired
    packs "Väike-Paala 4/2" in Tallinn found house 4 first. In Poland there is
    rarely a 14/3 to find, and house 14 is found as before.
    """
    found = read_query(q).numbers if q else []
    if not found:
        return []
    digits = {number: HOUSE_NUMBER_LEADING_DIGITS.match(number).group() for number in found}
    typed_twice = {lead for lead, count in Counter(digits[n] for n in found).items() if count > 1}
    numbers = sorted(digits)

    def unless_the_streets(lead, clause):
        if lead in typed_twice:
            return clause
        # the whole street name, with the number as a word of it: 3 in
        # "Aleja 3 Maja" or "3/5 Maja", not in "Aleja 30-lecia"
        in_street = {'regexp': {'street_name': '(.*[^0-9])?{}([^0-9].*)?'.format(lead)}}
        return {'bool': {'filter': [clause], 'must_not': [in_street]}}

    functions = []
    for number in numbers:
        house = HOUSE_PART.sub(r'\1', number)
        forms = sorted({number, number.lower(), number.upper(), house, house.lower(), house.upper()})
        functions.append({
            'filter': unless_the_streets(digits[number], {'terms': {'house_number': forms}}),
            'weight': HOUSE_NUMBER_WEIGHTS['house_number'],
        })
        if house != number:
            whole = sorted({number, number.lower(), number.upper()})
            functions.append({
                'filter': unless_the_streets(digits[number], {'terms': {'house_number': whole}}),
                'weight': HOUSE_NUMBER_WEIGHTS['whole_number'],
            })
        if digits[number] in typed_twice:
            functions.append({
                'filter': {'terms': {'house_number': forms}},
                'weight': HOUSE_NUMBER_WEIGHTS['typed_twice'],
            })
    for lead in sorted(set(digits.values()), key=int):
        if int(lead) > HOUSE_NUMBER_SORT_MAX:
            continue
        functions.append({
            'filter': unless_the_streets(lead, {'term': {'house_number_sort': int(lead)}}),
            'weight': HOUSE_NUMBER_WEIGHTS['house_number_sort'],
        })
    return functions


def exact_name_functions(q):
    """
    A scoring function for the locations whose whole name was typed, among other words.

    name.exact is matched against the whole query, and the city is in the
    query: "Berlin, Friedrichshain" found Friedrichshain-Kreuzberg first and
    Friedrichshain second, as a quarter of the districts the API check missed
    were found -- Krzyki-Partynice before Krzyki, Sendling-Westpark before
    Sendling. Each run of words typed is tried as a whole name -- but the
    whole query, which name.exact weighs already: typed whole, "Vila" is as
    likely the start of Vila Nova de Gaia as the village called Vila.
    """
    runs = word_runs(read_query(q).scored) if q else []
    runs = [run for run in runs if run != whole_run(q)]
    if not runs:
        return []
    return [{
        'filter': {'bool': {'should': [
            {'match': {field: run}} for run in runs for field in ('name.exact', 'names_all.exact')
        ]}},
        'weight': EXACT_NAME_WEIGHT,
    }]


def apply_scoring(query, q=None):
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

    Addresses have no size to tell them apart, and ``q``'s house number does
    it instead: see ``house_number_functions``.
    """
    functions = [
        {'filter': {'term': {'type': name}}, 'weight': weight}
        for name, weight in sorted(type_boosts().items())
    ]
    # Population where there is one, area otherwise -- the two filters are
    # complementary, and function_score skips a function whose filter does not
    # match, so exactly one of them applies to any hit. Appending both
    # unfiltered instead made them compound, and a hit carrying both signals
    # beat one carrying a single larger signal: Belfast, a node with 333,000
    # people and no polygon, scored 8.3 against 72.0 for Fermanagh and Omagh,
    # a district of 13,757 whose only advantage was having an area at all.
    # Most places are nodes -- 97.9% of the imported UK ones have no border --
    # so this was the common case rather than an edge one.
    has_population = {'range': {'population': {'gt': 0}}}
    functions.append({
        'filter': has_population,
        'field_value_factor': {
            'field': 'population',
            'modifier': 'log2p',
            'factor': 1.0,
            'missing': 0,
        }
    })
    functions.append({
        'filter': {'bool': {'must_not': has_population}},
        'field_value_factor': {
            'field': 'area',
            'modifier': 'log2p',
            'factor': 1.0,
            'missing': 0,
        }
    })
    functions.extend(exact_name_functions(q))
    functions.extend(house_number_functions(q))
    postcodes = read_query(q).postcodes if q else []
    if postcodes:
        functions.append({'filter': {'terms': {'postcode': postcodes}}, 'weight': POSTCODE_WEIGHT})
    return {
        'function_score': {
            'query': query,
            'functions': functions,
            'score_mode': 'multiply',
            'boost_mode': 'multiply',
        }
    }


# Along a street, in the order a person walks it: 1, 2, 3, 3 B, 5/7, 10, 12,
# 12a. The leading integer does the ordering and the raw keyword breaks the
# tie between 12 and 12a; anything with no digit to lead with -- "Unit 10",
# "Inside WH Smith", 0.2% at most in any country -- goes last. unmapped_type
# is needed for the same reason it is in SIZE_SORT: the same request can
# span the streets index, which has neither field.
NUMBER_SORT = [
    {'house_number_sort': {'order': 'asc', 'missing': '_last', 'unmapped_type': 'integer'}},
    {'house_number': {'order': 'asc', 'unmapped_type': 'keyword'}},
]


def build_sort(order, has_query, types=None):
    """
    ``?order=`` -> a sort clause.

    Relevance is the default when there is a query. Without one it would be
    meaningless, so size takes over: largest area, longest street -- except
    for a request that is only addresses, which have no size worth the name
    and whose natural order is the house number.
    """
    if order == 'name':
        return [{'name.keyword': {'order': 'asc', 'unmapped_type': 'keyword'}}]
    if order == 'number':
        return list(NUMBER_SORT)
    if order == 'size':
        return list(SIZE_SORT)
    if order == 'relevance' or (order is None and has_query):
        return ['_score']
    if order is None and types is not None and list(types) == ['addresses']:
        return list(NUMBER_SORT)
    return list(SIZE_SORT)


def build_body(params, types=None, strict=True):
    """
    Turn a request's query parameters into a full search body.

    ``types`` is the resolved list the view is searching, passed in rather
    than re-parsed from ``?type=`` because a view pinned to one type -- the
    ``/api/addresses/`` endpoint -- carries no such parameter.
    """
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
        strict=strict,
    )
    if q and (types is None or 'place-subareas' in types):
        # a place drawn as a point inside the district of its name is that
        # district, which answers for both: see utils.twins
        query['bool'].setdefault('must_not', []).append({'exists': {'field': TWIN_OF}})
    if q:
        # only meaningful when something is being scored; without a query the
        # results are sorted by size anyway
        query = apply_scoring(query, q)

    body = {
        'query': query,
        'sort': build_sort(params.get('order'), bool(q), types),
        'from': (page - 1) * page_size,
        'size': page_size,
        'track_total_hits': True,
    }
    if not include_geometry:
        body['_source'] = {'excludes': LIST_EXCLUDES}
    return body, page, page_size

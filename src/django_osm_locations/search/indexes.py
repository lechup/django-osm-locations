"""
Index definitions: what each location type looks like in OpenSearch.

One index per location type, each behind an alias. Readers only ever talk to the
alias, so a rebuild can swap the whole dataset atomically.
"""
import json

from django.contrib.gis.geos import GEOSGeometry

from ..models import Address, Country, CountrySubarea, Place, PlaceSubarea, Street
from ..utils.shared_slugs import SHARED_SLUG
from ..utils.twins import TWIN_OF, twins_of

# Prefix matching without a wildcard query: index the name as edge n-grams at
# write time so "krak" hits "krakow" through an ordinary term lookup. This is
# what replaces the Postgres search_vector columns.
ANALYSIS = {
    'analyzer': {
        'location_prefix': {
            'type': 'custom',
            'tokenizer': 'standard',
            'filter': ['lowercase', 'asciifolding', 'location_edge_ngram'],
        },
        # queries must not be n-grammed, only the indexed side
        'location_prefix_search': {
            'type': 'custom',
            'tokenizer': 'standard',
            'filter': ['lowercase', 'asciifolding'],
        },
        'location_exact': {
            'type': 'custom',
            'tokenizer': 'keyword',
            'filter': ['lowercase', 'asciifolding'],
        },
    },
    'filter': {
        'location_edge_ngram': {
            'type': 'edge_ngram',
            'min_gram': 1,
            'max_gram': 20,
        },
    },
}

SETTINGS = {
    'index': {
        'number_of_shards': 1,
        'number_of_replicas': 0,
        # edge n-grams make for long token streams on long street names
        'max_ngram_diff': 20,
    },
    'analysis': ANALYSIS,
}

# Whole words beside the prefixes. Prefixes are what let "krak" find Kraków,
# and also what let "Kraków" find powiat krakowski, "Lublin" Lubliniec and
# "Berlin" Berliner Straße: the city typed in full was a prefix of an
# adjective in the wrong place's ancestry or name, and that place came first --
# "Świdnicka Wrocław" in Kąty Wrocławskie, "Jesionowa Szczecin" in Szczecinek.
# A word found whole here outscores one found only as the start of another.
WORDS = {'type': 'text', 'analyzer': 'location_prefix_search'}

# Fields every location has.
BASE_PROPERTIES = {
    'id': {'type': 'long'},
    'type': {'type': 'keyword'},
    'name': {
        'type': 'text',
        'analyzer': 'location_prefix',
        'search_analyzer': 'location_prefix_search',
        'fields': {
            'exact': {'type': 'text', 'analyzer': 'location_exact'},
            'keyword': {'type': 'keyword'},
            'words': dict(WORDS),
        },
    },
    'slug': {'type': 'keyword'},
    'center': {'type': 'geo_point'},
    # Translations, for display. `enabled: false` stores and returns them
    # without indexing: a country carries 200-270 of them, and neither 270
    # mapping fields nor 270 inverted-index entries per country would earn
    # their keep when nothing queries them by language.
    'names': {'type': 'object', 'enabled': False},
    # Every translation as one searchable field, so "niemcy" finds
    # Deutschland without a per-language mapping. One value per translation:
    # joined into one string, "Warsaw" was a word of Warszawa's 200 names and
    # of powiat warszawski zachodni's few, and the shorter field won. ``exact``
    # is a whole translation, and without norms, so the number of them a place
    # has does not count against it.
    'names_all': {
        'type': 'text',
        'analyzer': 'location_prefix',
        'search_analyzer': 'location_prefix_search',
        'fields': {
            'exact': {'type': 'text', 'analyzer': 'location_exact', 'norms': False},
            'words': dict(WORDS, norms=False),
        },
    },
    'bbox': {'type': 'double'},
    'osm': {
        'properties': {
            'node_id': {'type': 'long'},
            'way_id': {'type': 'long'},
            'relation_id': {'type': 'long'},
            # those of the rows merged into this one, which the index leaves
            # out: an old slug ending in one of them is this location's
            'merged_node_ids': {'type': 'long'},
            'merged_way_ids': {'type': 'long'},
            'merged_relation_ids': {'type': 'long'},
        }
    },
    # Which OSM admin_level this came from. Without it a constituent country, a
    # county and a gmina are indistinguishable once several countries share an
    # index, since they all land in country-subareas.
    'admin_level': {'type': 'short'},
    # denormalised ancestry, searchable and filterable by slug
    'ancestry': {
        'type': 'nested',
        'properties': {
            'level': {'type': 'keyword'},
            'slug': {'type': 'keyword'},
            'name': {
                'type': 'text',
                'analyzer': 'location_prefix',
                'search_analyzer': 'location_prefix_search',
            },
        },
    },
    # flattened copies so a plain term filter can be used without a nested query
    'ancestry_slugs': {'type': 'keyword'},
    'ancestry_names': {
        'type': 'text',
        'analyzer': 'location_prefix',
        'search_analyzer': 'location_prefix_search',
        'fields': {'words': dict(WORDS)},
    },
    # The places among the ancestors, in whole words. A city is often called
    # what the region round it is -- Comunidad de Madrid, the province of
    # Palermo -- so in ancestry_names a suburb's house had the city's name as
    # much as the city's did, and its shorter ancestry won: "Calle de Granada
    # 28, Madrid" was the one in Leganés. Here only the city's houses have it.
    'place_names': dict(WORDS),
}

BORDER_PROPERTIES = {
    'area': {'type': 'double'},
    # Where the administration sits, which for a ring-shaped county is
    # outside the county. Distinct from `center`, which is always inside.
    'seat': {'type': 'geo_point'},
    'seat_name': {'type': 'keyword'},
    'population': {'type': 'long'},
    'geometry': {'type': 'geo_shape'},
}

ADDRESS_PROPERTIES = {
    # A point, and no extent: an address carries no geometry beyond where it
    # is, which is what makes 8.5 million of them affordable next to 1.9
    # million streets.
    'house_number': {'type': 'keyword'},
    # The leading integer, for ordering: a keyword sorts 1, 10, 11, 2 and an
    # integer cannot hold 12a. Kept beside the keyword, which breaks the tie
    # between 12 and 12a and is what gets displayed.
    'house_number_sort': {'type': 'integer'},
    'street_name': {'type': 'keyword'},
    'settlement_name': {'type': 'keyword'},
    'postcode': {'type': 'keyword'},
    # The slug the house shared with others, where it did: see
    # utils.shared_slugs. The detail endpoint names them all when asked for it.
    SHARED_SLUG: {'type': 'keyword'},
}

PLACE_PROPERTIES = dict(BORDER_PROPERTIES, **{
    # The district a place drawn as a point is the twin of: see utils.twins.
    TWIN_OF: {'type': 'keyword'},
})

MULTILINE_PROPERTIES = {
    'length': {'type': 'double'},
    'geometry': {'type': 'geo_shape'},
}


def clean_ancestry(entries, own_level=None, own_slug=None):
    """
    An ancestry without repeats and without the location itself.

    The recalculation used to list a district as its own ancestor, and every
    merged duplicate of an ancestor as a second one: "Kraków > Stare Miasto >
    Kazimierz > Stare Miasto > Kazimierz" for Kazimierz itself, and a fifth of
    Poland's streets with a district twice. It no longer does; this keeps a
    document right whatever the rows say, and is what repairs the packs built
    before. Order is kept: the first of a level is the one a slug was built
    from.
    """
    seen, kept = set(), []
    for entry in entries:
        key = (entry.get('level'), entry.get('slug'))
        if key in seen or (own_slug and key == (own_level, own_slug)):
            continue
        seen.add(key)
        kept.append(entry)
    return kept


#: The ``osm`` keys for what was merged into a location, as node, way, relation.
MERGED_OSM_IDS = ('merged_node_ids', 'merged_way_ids', 'merged_relation_ids')


def names_all(names):
    """Every translation once, in a stable order, or None when there is none."""
    return sorted(set(value for value in (names or {}).values() if value)) or None


def place_names(entries):
    """The names of the places in an ancestry, or None when it has none."""
    return [e['name'] for e in entries if e.get('level') == 'place' and e.get('name')] or None


def flatten_ancestry(entries):
    """``(entries, slugs, names)``: the entries, and what is searched of them."""
    slugs = [e['slug'] for e in entries if e.get('slug')]
    names = [e['name'] for e in entries if e.get('name')]
    return entries, slugs, names


class LocationIndex:
    """
    One location type's index: its mapping, its alias, and how to build a
    document from a model instance.
    """

    def __init__(self, name, model, geometry_field, extra_properties):
        self.name = name
        self.model = model
        self.geometry_field = geometry_field
        self.properties = dict(BASE_PROPERTIES)
        self.properties.update(extra_properties)

    def alias(self, prefix):
        return '{}-{}'.format(prefix, self.name)

    def index_name(self, prefix, suffix, country=None):
        """
        The concrete index behind the alias.

        A country token keeps one country's index apart from another's, so
        both can sit under the same alias and be searched together while
        still being built, published and replaced on their own.
        """
        if country:
            return '{}-{}-{}-{}'.format(prefix, self.name, country, suffix)
        return '{}-{}-{}'.format(prefix, self.name, suffix)

    def body(self):
        return {'settings': SETTINGS, 'mappings': {'properties': self.properties}}

    def queryset(self):
        """
        Rows to index.

        Merged rows are duplicates that the import resolved, so they are left
        out of the index. They stay in the database.
        """
        queryset = self.model.objects.all()
        if hasattr(self.model.objects, 'not_merged'):
            queryset = self.model.objects.not_merged()
        return queryset.order_by('id')

    @staticmethod
    def _geojson(geometry):
        if geometry is None:
            return None
        if not isinstance(geometry, GEOSGeometry):  # pragma: no cover - defensive
            return None
        return json.loads(geometry.geojson)

    def _ancestry(self, obj):
        """
        Denormalised ancestry as ``[[slug, name], ...]`` per level, flattened
        into something OpenSearch can filter and search.
        """
        entries = []
        for level in getattr(self.model, 'denorm_fields', ()):
            for pair in getattr(obj, level, None) or []:
                if not pair:
                    continue
                name = pair[0] if len(pair) > 0 else ''
                slug = pair[1] if len(pair) > 1 else ''
                entries.append({'level': level, 'slug': slug, 'name': name})
        return flatten_ancestry(clean_ancestry(
            entries, self.model.__name__.lower(), getattr(obj, 'slug', None)))

    def merged_osm_ids(self, ids):
        """
        ``{id: {'merged_node_ids': [...], ...}}`` for the rows among ``ids``
        that others were merged into: one query for a batch of documents.
        """
        if not hasattr(self.model, 'merged_into'):
            return {}
        found = {}
        rows = self.model.objects.filter(is_merged=True, merged_into__in=ids).values_list(
            'merged_into', 'node_osm_id', 'way_osm_id', 'relation_osm_id')
        for into, node, way, relation in rows:
            ids_ = found.setdefault(into, {key: [] for key in MERGED_OSM_IDS})
            for key, value in zip(MERGED_OSM_IDS, (node, way, relation)):
                if value is not None:
                    ids_[key].append(value)
        return {into: {key: sorted(set(values)) for key, values in ids_.items()}
                for into, ids_ in found.items()}

    def twin_fields(self, ids):
        """
        ``{id: fields}`` for the rows among ``ids`` that are, or have, a
        district's twin: one query for a batch of documents.
        """
        return twins_of(self.model.__name__.lower(), ids)

    def document(self, obj, merged=None, twin=None):
        """
        Build the OpenSearch document for one model instance.

        ``merged``: what ``merged_osm_ids`` found for it; ``twin``: what
        ``twin_fields`` did.
        """
        entries, slugs, names = self._ancestry(obj)
        doc = {
            'id': obj.id,
            'type': self.name,
            'name': obj.name,
            'names': obj.names or None,
            'names_all': names_all(obj.names),
            'slug': obj.slug,
            'center': (
                {'lon': obj.center[0], 'lat': obj.center[1]} if obj.center else None
            ),
            'bbox': list(obj.bbox or []),
            'osm': dict({
                'node_id': getattr(obj, 'node_osm_id', None),
                'way_id': getattr(obj, 'way_osm_id', None),
                'relation_id': getattr(obj, 'relation_osm_id', None),
            }, **(merged or {})),
            'admin_level': getattr(obj, 'admin_level', None),
            'ancestry': entries,
            'ancestry_slugs': slugs,
            'ancestry_names': names,
            'place_names': place_names(entries),
            # An address has no geometry field: its position is `center`, and
            # that is all there is of it.
            'geometry': self._geojson(
                getattr(obj, self.geometry_field, None) if self.geometry_field else None
            ),
        }
        if 'area' in self.properties:
            doc['area'] = obj.area
            doc['population'] = obj.population
            doc['seat'] = (
                {'lon': obj.seat[0], 'lat': obj.seat[1]} if obj.seat else None
            )
            doc['seat_name'] = obj.seat_name or None
            twin = twin or {}
            if TWIN_OF in self.properties:
                doc[TWIN_OF] = twin.get(TWIN_OF)
            if twin.get('population') and not obj.population:
                # the district answers for its twin, and ranks by its people
                doc['population'] = twin['population']
        if 'length' in self.properties:
            doc['length'] = obj.length
        if 'house_number' in self.properties:
            doc['house_number'] = obj.house_number
            doc['house_number_sort'] = obj.house_number_sort
            doc['street_name'] = obj.street_name or None
            doc['settlement_name'] = obj.settlement_name or None
            doc['postcode'] = obj.postcode or None
            # told apart from others that had it: see utils.shared_slugs
            derived = obj.slugify[:255]
            doc[SHARED_SLUG] = derived if obj.slug != derived else None
        return doc


INDEXES = {
    index.name: index
    for index in [
        LocationIndex('countries', Country, 'border', BORDER_PROPERTIES),
        LocationIndex('country-subareas', CountrySubarea, 'border', BORDER_PROPERTIES),
        LocationIndex('places', Place, 'border', PLACE_PROPERTIES),
        LocationIndex('place-subareas', PlaceSubarea, 'border', BORDER_PROPERTIES),
        LocationIndex('streets', Street, 'lines', MULTILINE_PROPERTIES),
        # No geometry field: `center` is on every document already, and an
        # address has nothing else worth indexing.
        LocationIndex('addresses', Address, None, ADDRESS_PROPERTIES),
    ]
}

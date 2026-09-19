"""
Index definitions: what each location type looks like in OpenSearch.

One index per location type, each behind an alias. Readers only ever talk to the
alias, so a rebuild can swap the whole dataset atomically.
"""
import json

from django.contrib.gis.geos import GEOSGeometry

from ..models import Country, CountrySubarea, Place, PlaceSubarea, Street

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
        },
    },
    'slug': {'type': 'keyword'},
    'center': {'type': 'geo_point'},
    'bbox': {'type': 'double'},
    'osm': {
        'properties': {
            'node_id': {'type': 'long'},
            'way_id': {'type': 'long'},
            'relation_id': {'type': 'long'},
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
    },
}

BORDER_PROPERTIES = {
    'area': {'type': 'double'},
    'population': {'type': 'long'},
    'geometry': {'type': 'geo_shape'},
}

MULTILINE_PROPERTIES = {
    'length': {'type': 'double'},
    'geometry': {'type': 'geo_shape'},
}


class LocationIndex:
    """
    One location type's index: its mapping, its alias, and how to build a
    document from a model instance.
    """

    def __init__(self, name, model, geometry_field, extra_properties, sort_field):
        self.name = name
        self.model = model
        self.geometry_field = geometry_field
        self.sort_field = sort_field
        self.properties = dict(BASE_PROPERTIES)
        self.properties.update(extra_properties)

    def alias(self, prefix):
        return '{}-{}'.format(prefix, self.name)

    def index_name(self, prefix, suffix):
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
        entries, slugs, names = [], [], []
        for level in getattr(self.model, 'denorm_fields', ()):
            for pair in getattr(obj, level, None) or []:
                if not pair:
                    continue
                name = pair[0] if len(pair) > 0 else ''
                slug = pair[1] if len(pair) > 1 else ''
                entries.append({'level': level, 'slug': slug, 'name': name})
                if slug:
                    slugs.append(slug)
                if name:
                    names.append(name)
        return entries, slugs, names

    def document(self, obj):
        """Build the OpenSearch document for one model instance."""
        entries, slugs, names = self._ancestry(obj)
        doc = {
            'id': obj.id,
            'type': self.name,
            'name': obj.name,
            'slug': obj.slug,
            'center': (
                {'lon': obj.center[0], 'lat': obj.center[1]} if obj.center else None
            ),
            'bbox': list(obj.bbox or []),
            'osm': {
                'node_id': getattr(obj, 'node_osm_id', None),
                'way_id': getattr(obj, 'way_osm_id', None),
                'relation_id': getattr(obj, 'relation_osm_id', None),
            },
            'admin_level': getattr(obj, 'admin_level', None),
            'ancestry': entries,
            'ancestry_slugs': slugs,
            'ancestry_names': names,
            'geometry': self._geojson(getattr(obj, self.geometry_field, None)),
        }
        if 'area' in self.properties:
            doc['area'] = obj.area
            doc['population'] = obj.population
        if 'length' in self.properties:
            doc['length'] = obj.length
        return doc


INDEXES = {
    index.name: index
    for index in [
        LocationIndex('countries', Country, 'border', BORDER_PROPERTIES, 'area'),
        LocationIndex('country-subareas', CountrySubarea, 'border', BORDER_PROPERTIES, 'area'),
        LocationIndex('places', Place, 'border', BORDER_PROPERTIES, 'area'),
        LocationIndex('place-subareas', PlaceSubarea, 'border', BORDER_PROPERTIES, 'area'),
        LocationIndex('streets', Street, 'lines', MULTILINE_PROPERTIES, 'length'),
    ]
}

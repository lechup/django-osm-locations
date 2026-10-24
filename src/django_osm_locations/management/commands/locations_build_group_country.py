"""
The one country document for a country imported a part at a time.

Each state of the United States is packed without the country, since 53 packs
each carrying "United States" with its own state's border would be 53
countries once loaded together. This builds the one document from what the
states did bring: their own state, a country subarea at level 4, whose borders
together are the country's.

    locations_build_group_country united-states            # index it, alias it
    locations_build_group_country united-states --snapshot # and snapshot it as us-<date>

Every state has to be loaded first (locations_import_snapshot --group us); a
border built from fewer is a smaller country, which is said, not refused.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from ...search.client import get_client, get_index_prefix
from ...search.indexer import new_index_suffix
from ...search.indexes import INDEXES
from ...search.snapshots import create, point_aliases, register_repository, repository_for
from .importers import get_importer, group_members


def country_document(importer, parts):
    """
    The document for ``importer``'s country from its parts' documents: each a
    dict with ``geometry`` (GeoJSON) and ``area``.
    """
    import json

    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    shapes = [shape(part['geometry']) for part in parts if part.get('geometry')]
    if not shapes:
        raise ValueError('no part has a border')
    # simplified, as a border is wanted for containment and display, and
    # the union of 53 coastlines is far bigger than either needs
    border = unary_union(shapes).simplify(0.0005, preserve_topology=True)
    west, south, east, north = border.bounds
    centre = border.representative_point()
    return {
        'type': 'countries',
        'name': importer.country_name,
        'names': None,
        'names_all': None,
        'slug': importer.country_slug,
        'center': {'lon': centre.x, 'lat': centre.y},
        'bbox': [west, south, east, north],
        'osm': {'node_id': None, 'way_id': None, 'relation_id': None},
        'admin_level': 2,
        'ancestry': [],
        'ancestry_slugs': [],
        'ancestry_names': [],
        'geometry': json.loads(json.dumps(mapping(border))),
        'area': sum(part.get('area') or 0 for part in parts),
        'population': None,
        'seat': None,
        'seat_name': None,
    }


class Command(BaseCommand):
    help = "Build the country document of a country imported a part at a time."

    def add_arguments(self, parser):
        parser.add_argument('group', help='The group: united-states.')
        parser.add_argument('--snapshot', action='store_true',
                            help='Snapshot the new index as <token>-<date>, ready to pack.')

    def handle(self, *args, **options):
        members = group_members(options['group'])
        if not members:
            raise CommandError('No group called {}.'.format(options['group']))
        importer = get_importer(members[0])
        token = importer.iso2.lower()
        client = get_client(timeout=300)
        prefix = get_index_prefix()

        hits = client.search(
            index=INDEXES['country-subareas'].alias(prefix),
            body={'size': len(members) * 2, '_source': ['name', 'geometry', 'area'],
                  'query': {'bool': {'filter': [
                      {'term': {'admin_level': 4}},
                      {'term': {'ancestry_slugs': importer.country_slug}}]}}},
        )['hits']['hits']
        parts = [hit['_source'] for hit in hits]
        self.stdout.write('{} of {} parts loaded'.format(len(parts), len(members)))
        if len(parts) < len(members):
            self.stdout.write('  the border will cover only those')

        document = country_document(importer, parts)
        index = INDEXES['countries'].index_name(prefix, new_index_suffix(), token)
        client.indices.create(index=index, body=INDEXES['countries'].body())
        client.index(index=index, id=importer.country_slug, body=document, refresh=True)
        point_aliases([index], client=client, prefix=prefix)
        self.stdout.write('{}: {}'.format(index, importer.country_name))

        if options['snapshot']:
            name = '{}-{}'.format(token, date.today().isoformat())
            repository, location = repository_for(token)
            register_repository(client=client, name=repository, location=location)
            state = create(name, [index], client=client, repository=repository)
            if state != 'SUCCESS':
                raise CommandError('Snapshot {} came out {}.'.format(name, state))
            self.stdout.write('snapshot {} in {}; make pack {}'.format(
                name, location, name))

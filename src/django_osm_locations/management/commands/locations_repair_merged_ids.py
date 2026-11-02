"""
Give a pack the OSM ids of what was merged into its locations, read from the
extract it was built from.

The index leaves a merged row out, and its OSM id with it, until the indexer
started writing those merged into a row (``osm.merged_*_ids``). Links to old
slugs -- ``<name>-<OSM id>``, which every duplicate but the biggest used to
get -- need them, and a pack built before has none. They are in the
extract: whatever the importer takes, by its own rules, that the pack has
no location for was merged into one of the same name nearby --

* a street's piece onto the line of the street, which holds its vertices;
* a village into the place whose border holds it, or one within 25 km, as
  the merge took them; a district into its namesake, a county that is a
  city into the city;
* a house into the one of its number on its street within 150 m -- the
  merge's 100 m between neighbours, and room for the centre it moved.

Each is looked for in the indexes, and its id written to the location
found. Something nowhere to be found -- outside the country, or a name the
import did not take -- is counted and left.

Like the other repairs this works on the live indexes, and a location that
has the id already is left alone, so it can be run again.

    manage.py locations_repair_merged_ids poland local/osm_data/europe/poland-latest.osm.pbf
"""
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from ...search.client import get_client, get_index_prefix
from ...search.indexes import INDEXES, MERGED_OSM_IDS
from .importers import get_importer
from .importers.base import _load_osmium, _location_index

#: What each kind of OSM object can have been merged into, its own type first.
TARGETS = {
    'street': ['streets'],
    'place': ['places', 'country-subareas'],
    'placesubarea': ['place-subareas'],
    'countrysubarea': ['country-subareas', 'places'],
    'address': ['addresses'],
}
#: The index a kind's own locations are in, for which ids it already has.
OWN = {'street': 'streets', 'place': 'places', 'placesubarea': 'place-subareas',
       'countrysubarea': 'country-subareas', 'address': 'addresses'}
KEY = {'node': 'merged_node_ids', 'way': 'merged_way_ids', 'relation': 'merged_relation_ids'}
OWN_FIELD = {'node': 'node_id', 'way': 'way_id', 'relation': 'relation_id'}

PLACE_KM = 25
HOUSE_METRES = 150
#: Around a vertex of a street's piece, in degrees: about two metres.
ON_THE_LINE = 0.00002


def envelope(lon, lat, pad):
    return {'type': 'envelope', 'coordinates': [[lon - pad, lat + pad], [lon + pad, lat - pad]]}


class Command(BaseCommand):
    help = "Write the OSM ids of merged rows onto the locations in the live indexes, from the extract."

    def add_arguments(self, parser):
        parser.add_argument('country', help="The importer's name, e.g. poland")
        parser.add_argument('extract', help='The .osm.pbf the pack was built from')
        parser.add_argument('--dry-run', action='store_true', help='Count; write nothing.')

    def handle(self, *args, **options):
        self.importer = get_importer(options['country'])
        if self.importer is None:
            raise CommandError('Unknown country: {}'.format(options['country']))
        self.client = get_client(timeout=600)
        self.prefix = get_index_prefix()
        self.dry_run = options['dry_run']
        own = {kind: self.own_ids(OWN[kind]) for kind in OWN}
        candidates = self.read_extract(options['extract'], own)
        found = defaultdict(lambda: defaultdict(set))
        for kind, items in candidates.items():
            matched = self.match(kind, items, found)
            self.stdout.write('{}: {} merged away, {} found where they went'.format(
                kind, len(items), matched))
        self.write(found)

    def alias(self, name):
        return INDEXES[name].alias(self.prefix)

    def own_ids(self, name):
        """``{'node': set, 'way': set, 'relation': set}``: what the index has locations for."""
        from opensearchpy.helpers import scan

        ids = {kind: set() for kind in OWN_FIELD}
        if not self.client.indices.exists(index=self.alias(name)):
            return ids
        for hit in scan(self.client, index=self.alias(name), size=5000,
                        query={'_source': ['osm'], 'query': {'match_all': {}}}):
            osm = hit['_source'].get('osm') or {}
            for kind, field in OWN_FIELD.items():
                if osm.get(field) is not None:
                    ids[kind].add(osm[field])
                ids[kind].update(osm.get(KEY[kind]) or [])
        return ids

    def read_extract(self, path, own):
        """
        Everything the importer would take that the pack has no location for.

        Relations first, for the nodes that say where they are -- the
        admin_centre or label -- then nodes and ways, located.
        """
        osmium = _load_osmium()
        imp = self.importer
        found = defaultdict(list)
        where_from = {}  # node id -> relation candidates it places
        for rel in osmium.FileProcessor(path, osmium.osm.RELATION):
            tags = {t.k: t.v for t in rel.tags}
            if 'name' not in tags:
                continue
            members = [(m.type, m.ref, m.role) for m in rel.members]
            for kind, test in (('countrysubarea', imp._is_relation_countrysubarea),
                               ('place', imp._is_relation_place),
                               ('placesubarea', imp._is_relation_placesubarea),
                               ('street', imp._is_relation_street)):
                if not test(rel.id, tags, members):
                    continue
                if rel.id not in own[kind]['relation']:
                    item = {'kind': 'relation', 'id': rel.id, 'name': tags['name'], 'at': None}
                    found[kind].append(item)
                    for mtype, ref, role in members:
                        if mtype == 'n' and role in ('admin_centre', 'label'):
                            where_from.setdefault(ref, []).append(item)
                break

        with _location_index() as storage:
            processor = (osmium.FileProcessor(path, osmium.osm.NODE | osmium.osm.WAY)
                         .with_locations(storage)
                         .with_filter(osmium.filter.KeyFilter('place', 'highway', 'addr:housenumber')))
            for obj in processor:
                tags = {t.k: t.v for t in obj.tags}
                if obj.is_node():
                    if not obj.location.valid():
                        continue
                    at = (obj.location.lon, obj.location.lat)
                    for item in where_from.get(obj.id, ()):
                        item['at'] = item['at'] or at
                    if imp._is_node_place(obj.id, tags, at) and obj.id not in own['place']['node']:
                        found['place'].append({'kind': 'node', 'id': obj.id, 'name': tags['name'], 'at': at})
                    if imp._is_address(tags) and obj.id not in own['address']['node']:
                        found['address'].append(self.house('node', obj.id, tags, at))
                    continue
                points = [(n.location.lon, n.location.lat) for n in obj.nodes if n.location.valid()]
                if not points:
                    continue
                refs = [n.ref for n in obj.nodes]
                middle = sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)
                if imp._is_way_street(obj.id, tags, refs) and obj.id not in own['street']['way']:
                    # its own vertices, which the line it was joined into holds
                    found['street'].append({'kind': 'way', 'id': obj.id, 'name': tags['name'],
                                            'at': points[len(points) // 2]})
                if imp._is_way_place(obj.id, tags, refs) and obj.id not in own['place']['way']:
                    found['place'].append({'kind': 'way', 'id': obj.id, 'name': tags['name'], 'at': middle})
                if imp._is_address(tags) and obj.id not in own['address']['way']:
                    found['address'].append(self.house('way', obj.id, tags, middle))
        return found

    def house(self, kind, osm_id, tags, at):
        """An address as the importer reads one: see BaseImporter._address_defaults."""
        number = self.importer._fit_house_number(tags.get('addr:housenumber'))
        return {'kind': kind, 'id': osm_id, 'at': at, 'number': number,
                'street': tags.get('addr:street') or '',
                'settlement': tags.get('addr:city') or tags.get('addr:place') or ''}

    def query(self, kind, item):
        if kind == 'address':
            filters = [{'term': {'house_number': item['number']}},
                       {'geo_distance': {'distance': '{}m'.format(HOUSE_METRES),
                                         'center': {'lon': item['at'][0], 'lat': item['at'][1]}}}]
            if item['street']:
                filters.append({'term': {'street_name': item['street']}})
            else:
                filters.append({'term': {'settlement_name': item['settlement']}})
            return {'bool': {'filter': filters}}, True
        name = {'term': {'name.keyword': item['name']}}
        if item['at'] is None:
            # nothing says where: only a name no other location shares will do
            return {'bool': {'filter': [name]}}, False
        lon, lat = item['at']
        if kind == 'street':
            return {'bool': {'filter': [name, {'geo_shape': {'geometry': {
                'shape': envelope(lon, lat, ON_THE_LINE), 'relation': 'intersects'}}}]}}, True
        return {'bool': {'filter': [name], 'should': [
            {'geo_shape': {'geometry': {'shape': {'type': 'point', 'coordinates': [lon, lat]},
                                        'relation': 'intersects'}}},
            {'geo_distance': {'distance': '{}km'.format(PLACE_KM), 'center': {'lon': lon, 'lat': lat}}},
        ], 'minimum_should_match': 1}}, True

    def match(self, kind, items, found):
        """Find where each went; returns how many were found."""
        index = ','.join(self.alias(name) for name in TARGETS[kind]
                         if self.client.indices.exists(index=self.alias(name)))
        if not index:
            return 0
        matched = 0
        for start in range(0, len(items), 200):
            batch = items[start:start + 200]
            body = []
            for item in batch:
                query, located = self.query(kind, item)
                search = {'size': 2, '_source': False, 'query': query}
                if located:
                    # inside a border first, then the nearest
                    search['sort'] = ['_score', {'_geo_distance': {
                        'center': {'lon': item['at'][0], 'lat': item['at'][1]}, 'order': 'asc'}}]
                body.extend([{'index': index, 'ignore_unavailable': True}, search])
            responses = self.client.msearch(body=body)['responses']
            for item, response in zip(batch, responses):
                hits = response.get('hits', {}).get('hits', [])
                located = item.get('at') is not None
                if not hits or (not located and len(hits) > 1):
                    continue
                found[(hits[0]['_index'], hits[0]['_id'])][KEY[item['kind']]].add(item['id'])
                matched += 1
        return matched

    def write(self, found):
        from opensearchpy.helpers import bulk

        targets = sorted(found)
        written = 0
        for name in ('streets', 'places', 'place-subareas', 'country-subareas', 'addresses'):
            if not self.dry_run and self.client.indices.exists(index=self.alias(name)):
                self.client.indices.put_mapping(index=self.alias(name), body={
                    'properties': {'osm': INDEXES[name].properties['osm']}})
        for start in range(0, len(targets), 1000):
            batch = targets[start:start + 1000]
            docs = self.client.mget(body={'docs': [
                {'_index': index, '_id': pk, '_source': ['osm']} for index, pk in batch]})['docs']
            actions = []
            for (index, pk), doc in zip(batch, docs):
                osm = dict(doc.get('_source', {}).get('osm') or {})
                changed = False
                for key in MERGED_OSM_IDS:
                    have = set(osm.get(key) or [])
                    more = found[(index, pk)].get(key, set()) - have
                    if more:
                        osm[key] = sorted(have | more)
                        changed = True
                if changed:
                    actions.append({'_op_type': 'update', '_index': index, '_id': pk,
                                    'doc': {'osm': osm}})
            written += len(actions)
            if actions and not self.dry_run:
                bulk(self.client, actions, chunk_size=1000)
        if not self.dry_run:
            self.client.indices.refresh(index='{}-*'.format(self.prefix))
        self.stdout.write('{} locations given the ids of what was merged into them'.format(written))

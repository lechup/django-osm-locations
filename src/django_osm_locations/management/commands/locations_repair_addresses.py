"""
Join each street's pieces, link addresses to the street they name in their
place, and keep one address for each house -- in a pack's indexes.

The recalculation does all three now (merge_namesake_streets,
link_addresses_to_streets_in_their_place, merge_repeated_houses); this does
them for the packs built before it did, without the database they came from,
by the same rules: see utils.duplicates. Like locations_repair_ancestry it
works on the live indexes, so a pack is repaired by restoring it, running
this, and snapshotting it again.

A street that stays keeps the shortest slug of its pieces, which is the one
without an OSM id; an address that gains a street, or whose street was a
piece, gains a slug from it, as the recalculation derives one. The rest are
deleted from the index, their OSM ids kept on the one that stays, as the
indexer writes those merged into a row.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand

from ...search.client import get_client, get_index_prefix
from ...search.indexes import INDEXES, MERGED_OSM_IDS, flatten_ancestry, names_all
from ...utils.duplicates import clusters, keeper, merged_center, merged_postcode
from ...utils.slugify import slugify

#: An address naming a street and linked to none.
UNLINKED = {'bool': {
    'filter': [{'exists': {'field': 'street_name'}}],
    'must_not': [{'term': {'street_name': ''}},
                 {'nested': {'path': 'ancestry', 'query': {'term': {'ancestry.level': 'street'}}}}],
}}

#: The address's box around its point, as Address.near_bbox makes it.
NEAR = 0.009


def last(entries, level):
    found = [e for e in entries or [] if e.get('level') == level]
    return found[-1] if found else None


def address_slug(parent_slug, number):
    """Address.slugify, for a parent known by its slug."""
    return slugify('{}-{}'.format(parent_slug, number))[:255]


def with_street(source, street):
    """An address's fields with ``street`` -- ``(name, slug)`` -- as its street."""
    entries = [e for e in source.get('ancestry') or [] if e.get('level') != 'street']
    entries.append({'level': 'street', 'slug': street[1], 'name': street[0]})
    entries, slugs, names = flatten_ancestry(entries)
    return {'ancestry': entries, 'ancestry_slugs': slugs, 'ancestry_names': names,
            'slug': address_slug(street[1], source.get('house_number') or '')}


def merged_osm(stays, gone):
    """
    The ``osm`` of the document that stays, with the OSM ids of those that go.

    As the indexer writes them from merged_into: an old slug ending in a
    piece's id is the street that stays.
    """
    osm = dict(stays.get('osm') or {})
    for key, own in zip(MERGED_OSM_IDS, ('node_id', 'way_id', 'relation_id')):
        ids = set(osm.get(key) or [])
        for source in gone:
            theirs = source.get('osm') or {}
            ids.update(theirs.get(key) or [])
            if theirs.get(own) is not None:
                ids.add(theirs[own])
        if ids:
            osm[key] = sorted(ids)
    return osm


def joined_street(pieces):
    """
    The fields of the street that stays, and which piece it is.

    ``pieces`` are the documents' sources. The lines are joined as the
    recalculation joins them -- union, then merged where they touch -- and
    the length is theirs, less what overlapped.
    """
    from shapely.geometry import MultiLineString, mapping, shape
    from shapely.ops import linemerge, unary_union

    stays = min(range(len(pieces)), key=lambda i: (len(pieces[i]['slug']), pieces[i]['slug']))
    shapes = [shape(p['geometry']) for p in pieces if p.get('geometry')]
    lines = [line for s in shapes for line in getattr(s, 'geoms', [s])]
    union = unary_union(MultiLineString(lines))
    merged = linemerge(union) if union.geom_type != 'LineString' else union
    if merged.geom_type == 'LineString':
        merged = MultiLineString([merged])
    planar = sum(line.length for line in lines) or 1.0
    names = {}
    for piece in reversed([pieces[stays]] + pieces):
        names.update(piece.get('names') or {})
    point = merged.representative_point()
    return stays, {
        'osm': merged_osm(pieces[stays], [p for i, p in enumerate(pieces) if i != stays]),
        'geometry': mapping(merged),
        'length': sum(p.get('length') or 0 for p in pieces) * merged.length / planar,
        'center': {'lon': point.x, 'lat': point.y},
        'bbox': list(merged.bounds),
        'names': names or None,
        'names_all': names_all(names),
    }


class Command(BaseCommand):
    help = ("Join each street's pieces, link addresses to the street they name in their "
            "place, and keep one address for each house, in the live indexes.")

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Count what would change; write nothing.')

    def handle(self, *args, **options):
        self.client = get_client(timeout=600)
        prefix = get_index_prefix()
        self.streets = '{}-streets'.format(prefix)
        self.addresses = '{}-addresses'.format(prefix)
        self.dry_run = options['dry_run']
        if not (self.client.indices.exists(index=self.streets)
                and self.client.indices.exists(index=self.addresses)):
            self.stdout.write('no streets or no addresses here: nothing to repair')
            return
        if not self.dry_run:
            # the fields for what goes, mapped as the index definitions map them
            for name, index in (('streets', self.streets), ('addresses', self.addresses)):
                self.client.indices.put_mapping(index=index, body={
                    'properties': {'osm': INDEXES[name].properties['osm']}})
        by_name = self.join_streets()
        self.link_addresses(by_name)
        self.one_address_for_each_house()
        if not self.dry_run:
            for index in (self.streets, self.addresses):
                self.client.indices.forcemerge(index=index, only_expunge_deletes=True)
            self.stdout.write('deleted documents expunged')

    def write(self, index, actions):
        from opensearchpy.helpers import bulk

        if actions and not self.dry_run:
            bulk(self.client, actions, chunk_size=1000, refresh=False)
            self.client.indices.refresh(index=index)

    def scan(self, index, query, fields):
        from opensearchpy.helpers import scan

        return scan(self.client, index=index, size=2000,
                    query={'_source': fields, 'query': query})

    def join_streets(self):
        """One street of each name in each place; returns (name, place slug) -> (name, slug)."""
        # where each street is and what it is called, and nothing more: a
        # country's streets run to a million and more
        groups = defaultdict(list)
        for hit in self.scan(self.streets, {'match_all': {}}, ['name', 'slug', 'ancestry']):
            place = last(hit['_source'].get('ancestry'), 'place')
            if place:
                groups[(hit['_source']['name'], place['slug'])].append(
                    (hit['_index'], hit['_id'], hit['_source']['name'], hit['_source']['slug']))
        by_name, actions, gone = {}, [], {}
        for key, found in groups.items():
            if len(found) > 1:
                docs = self.client.mget(body={'docs': [
                    {'_index': index, '_id': pk} for index, pk, _, _ in found]})['docs']
                pieces = [d['_source'] for d in docs]
                stays, fields = joined_street(pieces)
                actions.append({'_op_type': 'update', '_index': docs[stays]['_index'],
                                '_id': docs[stays]['_id'], 'doc': fields})
                for i, doc in enumerate(docs):
                    if i != stays:
                        actions.append({'_op_type': 'delete', '_index': doc['_index'],
                                        '_id': doc['_id']})
                        gone[pieces[i]['slug']] = (pieces[stays]['name'], pieces[stays]['slug'])
                by_name[key] = (pieces[stays]['name'], pieces[stays]['slug'])
            else:
                by_name[key] = found[0][2:]
        self.write(self.streets, actions)
        self.stdout.write('streets: {} names joined from {} more pieces'.format(
            len(actions) - len(gone), len(gone)))

        # the houses on a piece that went are on the street that stays
        moved, slugs = [], sorted(gone)
        for start in range(0, len(slugs), 5000):
            batch = slugs[start:start + 5000]
            query = {'nested': {'path': 'ancestry', 'query': {'bool': {'filter': [
                {'term': {'ancestry.level': 'street'}}, {'terms': {'ancestry.slug': batch}}]}}}}
            for hit in self.scan(self.addresses, query, ['ancestry', 'house_number']):
                street = last(hit['_source']['ancestry'], 'street')
                moved.append({'_op_type': 'update', '_index': hit['_index'], '_id': hit['_id'],
                              'doc': with_street(hit['_source'], gone[street['slug']])})
        self.write(self.addresses, moved)
        self.stdout.write('addresses: {} moved to the street that stays'.format(len(moved)))
        return by_name

    def link_addresses(self, by_name):
        actions, total = [], 0
        for hit in self.scan(self.addresses, UNLINKED, ['ancestry', 'house_number', 'street_name']):
            total += 1
            place = last(hit['_source'].get('ancestry'), 'place')
            street = place and by_name.get((hit['_source']['street_name'], place['slug']))
            if street:
                actions.append({'_op_type': 'update', '_index': hit['_index'], '_id': hit['_id'],
                                'doc': with_street(hit['_source'], street)})
        self.write(self.addresses, actions)
        self.stdout.write('addresses: {} of {} with no street linked to the one in their place'.format(
            len(actions), total))

    def runs(self):
        """Every address, a slug at a time, in id order within it."""
        after = None
        while True:
            body = {'size': 5000, 'sort': [{'slug': 'asc'}, {'id': 'asc'}],
                    '_source': ['id', 'slug', 'ancestry', 'street_name', 'center', 'osm', 'postcode'],
                    'query': {'match_all': {}}}
            if after:
                body['search_after'] = after
            hits = self.client.search(index=self.addresses, body=body)['hits']['hits']
            if not hits:
                return
            yield from hits
            after = hits[-1]['sort']

    def one_address_for_each_house(self):
        actions, houses, merged = [], 0, 0

        def resolve(run):
            nonlocal houses, merged
            groups = defaultdict(list)
            for hit in run:
                linked = last(hit['_source'].get('ancestry'), 'street')
                groups['' if linked else hit['_source'].get('street_name') or ''].append(hit)
            for hits in groups.values():
                points = [(h['_source']['center']['lon'], h['_source']['center']['lat']) for h in hits]
                for members in clusters(points):
                    if len(members) < 2:
                        continue
                    chosen = [hits[i] for i in members]
                    outlines = [bool((h['_source'].get('osm') or {}).get('way_id')) for h in chosen]
                    stays = chosen[keeper(outlines)]
                    lon, lat = merged_center([points[i] for i in members], outlines)
                    gone = [h['_source'] for h in chosen if h is not stays]
                    actions.append({'_op_type': 'update', '_index': stays['_index'], '_id': stays['_id'],
                                    'doc': {'center': {'lon': lon, 'lat': lat},
                                            'osm': merged_osm(stays['_source'], gone),
                                            'bbox': [lon - NEAR, lat - NEAR, lon + NEAR, lat + NEAR],
                                            # as the indexer writes none: None
                                            'postcode': merged_postcode(
                                                [h['_source'].get('postcode') or '' for h in chosen])
                                            or None}})
                    for hit in chosen:
                        if hit is not stays:
                            actions.append({'_op_type': 'delete', '_index': hit['_index'],
                                            '_id': hit['_id']})
                    houses += 1
                    merged += len(chosen) - 1

        run, slug = [], None
        for hit in self.runs():
            if hit['_source'].get('slug') != slug:
                if len(run) > 1:
                    resolve(run)
                run, slug = [], hit['_source'].get('slug')
            if hit['_source'].get('center'):
                run.append(hit)
        if len(run) > 1:
            resolve(run)
        self.write(self.addresses, actions)
        self.stdout.write('addresses: {} houses from {} more rows'.format(houses, merged))

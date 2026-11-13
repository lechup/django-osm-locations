"""
Tell apart the houses that share a slug, in a pack's indexes.

The recalculation does it now (qualify_shared_address_slugs); this does it
for the packs built before it did, without the database they came from, by
the same rule: see utils.shared_slugs. Like locations_repair_ancestry it
works on the live indexes, so a pack is repaired by restoring it, running
this, and snapshotting it again -- after locations_repair_addresses, which
moves houses between streets and gives them their street's slug back.

Run again, it changes nothing more, and a house that shares its slug with no
other any more has it back bare.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand

from ...search.client import get_client, get_index_prefix
from ...search.indexes import INDEXES
from ...utils.shared_slugs import NEAREST_PLACE_METRES, SHARED_SLUG, qualifiers, qualify
from ...utils.slugify import slugify
from .locations_repair_addresses import address_slug, first, last, update

#: Base slugs looked at in one go.
BATCH = 1000

#: Houses whose nearest place is asked for in one request. OpenSearch 2.18
#: answers a multi-search with a TASK_RESOURCE_USAGE header for about one
#: search in ten, and Python's http.client refuses a response with more than
#: 100 headers: one request for the 1,941 houses of boras-kommun-1 failed.
NEAREST_BATCH = 200

FIELDS = ['id', 'slug', SHARED_SLUG, 'name', 'house_number', 'street_name', 'settlement_name',
          'ancestry', 'center', 'osm']


def derived(source):
    """The slug an address has before it is told apart: Address.slugify, from the index."""
    for level in ('street', 'place'):
        entry = first(source.get('ancestry'), level)
        if entry and entry.get('slug'):
            return address_slug(entry['slug'], source.get('house_number') or '')
    return slugify(source.get('name') or '')[:255]


def osm_id(source):
    osm = source.get('osm') or {}
    return osm.get('way_id') or osm.get('node_id') or osm.get('relation_id') or source.get('id')


class Command(BaseCommand):
    help = "Give each house sharing a slug one of its own, in the live indexes."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Count what would change; write nothing.')

    def handle(self, *args, **options):
        self.client = get_client(timeout=600)
        prefix = get_index_prefix()
        self.addresses = INDEXES['addresses'].alias(prefix)
        self.places = INDEXES['places'].alias(prefix)
        dry_run = options['dry_run']
        if not self.client.indices.exists(index=self.addresses):
            self.stdout.write('no addresses here: nothing to repair')
            return
        if not dry_run:
            self.client.indices.put_mapping(index=self.addresses, body={
                'properties': {SHARED_SLUG: INDEXES['addresses'].properties[SHARED_SLUG]}})

        bases = sorted(self.shared('slug') | self.held(SHARED_SLUG))
        actions, seen, done, groups, houses = [], set(), set(), 0, 0
        for start in range(0, len(bases), BATCH):
            batch = [base for base in bases[start:start + BATCH] if base not in seen]
            by_base = defaultdict(dict)
            for hit in self.holding(batch):
                by_base[derived(hit['_source'])][hit['_id']] = hit
            # a house whose slug is another base's -- its street changed
            # since -- is told apart with that base's houses
            others = sorted(set(by_base) - set(batch) - seen)
            if others:
                for hit in self.holding(others):
                    by_base[derived(hit['_source'])][hit['_id']] = hit
            for base, held in sorted(by_base.items()):
                if base in seen:
                    continue
                seen.add(base)
                hits = sorted((hit for pk, hit in held.items() if pk not in done),
                              key=lambda h: (h['_source'].get('id') or 0, h['_id']))
                done.update(hit['_id'] for hit in hits)
                if not hits:
                    continue
                if len(hits) == 1:
                    wanted = [(base, None)]
                else:
                    groups += 1
                    houses += len(hits)
                    wanted = [(slug, base) for slug in qualify(
                        base, self.houses(hits), self.taken(base, hits))]
                for hit, (slug, shared) in zip(hits, wanted):
                    if (hit['_source'].get('slug'), hit['_source'].get(SHARED_SLUG)) != (slug, shared):
                        actions.append(update(hit, {'slug': slug, SHARED_SLUG: shared}))
        if actions and not dry_run:
            from opensearchpy.helpers import bulk

            bulk(self.client, actions, chunk_size=1000, refresh=True)
        self.stdout.write('addresses: {} slugs shared by {} houses, {} rewritten'.format(
            groups, houses, len(actions)))

    def values(self, field, minimum):
        """The values of ``field`` that ``minimum`` or more documents hold."""
        found, after = set(), None
        while True:
            composite = {'size': 10000, 'sources': [{'v': {'terms': {'field': field}}}]}
            if after:
                composite['after'] = after
            result = self.client.search(index=self.addresses, body={
                'size': 0, 'aggs': {'d': {'composite': composite}}})
            buckets = result['aggregations']['d']['buckets']
            found.update(b['key']['v'] for b in buckets if b['doc_count'] >= minimum)
            after = result['aggregations']['d'].get('after_key')
            if not after or not buckets:
                return found

    def shared(self, field):
        return self.values(field, 2)

    def held(self, field):
        return self.values(field, 1)

    def holding(self, bases):
        from opensearchpy.helpers import scan

        query = {'bool': {'should': [{'terms': {'slug': bases}}, {'terms': {SHARED_SLUG: bases}}],
                          'minimum_should_match': 1}}
        return scan(self.client, index=self.addresses, size=2000,
                    query={'_source': FIELDS, 'query': query})

    def taken(self, base, hits):
        """The slugs past ``base`` that houses other than ``hits`` have."""
        mine = {hit['_id'] for hit in hits}
        found = self.client.search(index=self.addresses, body={
            'size': 10000, '_source': False, 'docvalue_fields': ['slug'],
            'query': {'prefix': {'slug': base + '-'}}})['hits']['hits']
        return {hit['fields']['slug'][0] for hit in found if hit['_id'] not in mine}

    def houses(self, hits):
        """What tells each house apart, the nearest place only where the rest does not."""
        houses = []
        for hit in hits:
            source = hit['_source']
            district = last(source.get('ancestry'), 'placesubarea')
            houses.append({
                'linked': bool(last(source.get('ancestry'), 'street')),
                'street_name': source.get('street_name'),
                'settlement': source.get('settlement_name'),
                'district': district and district.get('name'),
                'osm_id': osm_id(source),
            })
        told = [tuple(list(qualifiers(house))[:-1]) for house in houses]
        if len(set(told)) < len(told):
            for house, nearest in zip(houses, self.nearest_places(hits)):
                house['nearest'] = nearest
        return houses

    def nearest_places(self, hits):
        """The name of the place nearest each house within its own, or None."""
        found = []
        for start in range(0, len(hits), NEAREST_BATCH):
            body = []
            for hit in hits[start:start + NEAREST_BATCH]:
                center = hit['_source'].get('center') or {'lon': 0, 'lat': 0}
                place = last(hit['_source'].get('ancestry'), 'place')
                body.append({'index': self.places})
                body.append({'size': 1, '_source': ['name'], 'query': {'bool': {'filter': [
                    {'nested': {'path': 'ancestry', 'query': {'bool': {'filter': [
                        {'term': {'ancestry.level': 'place'}},
                        {'term': {'ancestry.slug': place['slug'] if place else ''}}]}}}},
                    {'geo_distance': {'distance': '{}m'.format(NEAREST_PLACE_METRES),
                                      'center': center}},
                ]}}, 'sort': [{'_geo_distance': {'center': center, 'order': 'asc'}}]})
            for response in self.client.msearch(body=body)['responses']:
                hits_ = response.get('hits', {}).get('hits', [])
                found.append(hits_[0]['_source']['name'] if hits_ else None)
        return found

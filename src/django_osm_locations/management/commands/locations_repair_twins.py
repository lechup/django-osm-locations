"""
Mark each place that is a district's twin, and give the district its
population, in a pack's indexes.

The indexer does both now, from the database (utils.twins); this does them
for the packs indexed before it did, without the database they came from, by
the same rule: a place with no border, inside the border of a district of the
same name in the same city. Like locations_repair_ancestry it works on the
live indexes, so a pack is repaired by restoring it, running this, and
snapshotting it again. Run again, it changes nothing more.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand

from ...search.client import get_client, get_index_prefix
from ...search.indexes import INDEXES
from ...utils.twins import TWIN_OF, innermost_place, name_key

#: Points tested against their districts' borders in one request: few enough
#: that OpenSearch's TASK_RESOURCE_USAGE headers, about one per ten searches,
#: stay under the 100 Python's http.client accepts (see locations_repair_slugs).
BATCH = 200


class Command(BaseCommand):
    help = "Mark the places that are a district's twin, in the live indexes."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Count what would change; write nothing.')

    def handle(self, *args, **options):
        self.client = get_client(timeout=600)
        prefix = get_index_prefix()
        places = INDEXES['places'].alias(prefix)
        districts = INDEXES['place-subareas'].alias(prefix)
        dry_run = options['dry_run']
        if not (self.client.indices.exists(index=places)
                and self.client.indices.exists(index=districts)):
            self.stdout.write('no places or no place subareas here: nothing to repair')
            return
        if not dry_run:
            self.client.indices.put_mapping(index=places, body={
                'properties': {TWIN_OF: INDEXES['places'].properties[TWIN_OF]}})

        named = defaultdict(list)
        for hit in self.scan(districts, ['id', 'name', 'slug', 'ancestry', 'population']):
            city = innermost_place(hit['_source'].get('ancestry'))
            if city:
                named[(name_key(hit['_source'].get('name')), city)].append(hit)

        candidates, marked = [], []
        for hit in self.scan(places, ['id', 'name', 'ancestry', 'area', 'center',
                                      'population', TWIN_OF]):
            source = hit['_source']
            if source.get(TWIN_OF):
                marked.append(hit)
            city = innermost_place(source.get('ancestry'))
            if city and not source.get('area') and source.get('center'):
                around = named.get((name_key(source.get('name')), city))
                if around:
                    candidates.append(
                        (hit, sorted(around, key=lambda d: d['_source'].get('id') or 0)))

        twins = self.inside(candidates)
        actions, populations = [], defaultdict(int)
        for place, district in twins.values():
            slug = district['_source']['slug']
            if place['_source'].get(TWIN_OF) != slug:
                actions.append(update(place, {TWIN_OF: slug}))
            population = place['_source'].get('population') or 0
            if population and not district['_source'].get('population'):
                key = (district['_index'], district['_id'])
                populations[key] = max(populations[key], population)
        actions.extend(update(hit, {TWIN_OF: None}) for hit in marked if hit['_id'] not in twins)
        marks = len(actions)
        actions.extend({'_op_type': 'update', '_index': index, '_id': pk,
                        'doc': {'population': population}}
                       for (index, pk), population in sorted(populations.items()))
        if actions and not dry_run:
            from opensearchpy.helpers import bulk

            bulk(self.client, actions, chunk_size=1000, refresh=True)
        self.stdout.write(
            "places: {} twins of a district, {} marked or unmarked; districts: {} given "
            "their twin's population".format(len(twins), marks, len(populations)))

    def scan(self, index, fields):
        from opensearchpy.helpers import scan

        return scan(self.client, index=index, size=2000,
                    query={'_source': fields, 'query': {'match_all': {}}})

    def inside(self, candidates):
        """
        ``{place _id: (place, district)}``: of the districts named like each
        point, the first, by id, whose border is around it.
        """
        pairs = [(hit, district) for hit, around in candidates for district in around]
        found = {}
        for start in range(0, len(pairs), BATCH):
            batch = pairs[start:start + BATCH]
            body = []
            for hit, district in batch:
                center = hit['_source']['center']
                body.append({'index': district['_index']})
                body.append({'size': 0, 'track_total_hits': True, 'query': {'bool': {'filter': [
                    {'ids': {'values': [district['_id']]}},
                    {'geo_shape': {'geometry': {'shape': {
                        'type': 'point', 'coordinates': [center['lon'], center['lat']]},
                        'relation': 'intersects'}}},
                ]}}})
            for (hit, district), response in zip(batch, self.client.msearch(body=body)['responses']):
                if 'error' in response:
                    raise RuntimeError('{}: {}'.format(district['_id'], response['error']))
                if response['hits']['total']['value'] and hit['_id'] not in found:
                    found[hit['_id']] = (hit, district)
        return found


def update(hit, doc):
    return {'_op_type': 'update', '_index': hit['_index'], '_id': hit['_id'], 'doc': doc}

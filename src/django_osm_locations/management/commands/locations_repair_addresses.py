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
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand

from ...search.client import get_client, get_index_prefix
from ...search.indexes import INDEXES, MERGED_OSM_IDS, flatten_ancestry, names_all, place_names
from ...utils.duplicates import clusters, keeper, merged_center, merged_postcode
from ...utils.settlements import nearest, settlement_key, streets
from ...utils.shared_slugs import SHARED_SLUG, SLUG_LENGTH, qualified
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


def first(entries, level):
    found = [e for e in entries or [] if e.get('level') == level]
    return found[0] if found else None


def update(hit, doc):
    return {'_op_type': 'update', '_index': hit['_index'], '_id': hit['_id'], 'doc': doc}


def delete(hit):
    return {'_op_type': 'delete', '_index': hit['_index'], '_id': hit['_id']}


def address_slug(parent_slug, number):
    """Address.slugify, for a parent known by its slug."""
    return slugify('{}-{}'.format(parent_slug, number))[:255]


def with_street(source, street):
    """An address's fields with ``street`` -- ``(name, slug)`` -- as its street."""
    entries = [e for e in source.get('ancestry') or [] if e.get('level') != 'street']
    entries.append({'level': 'street', 'slug': street[1], 'name': street[0]})
    entries, slugs, names = flatten_ancestry(entries)
    # the slug it shared, if it did, was the one of the street it leaves
    return {'ancestry': entries, 'ancestry_slugs': slugs, 'ancestry_names': names,
            'slug': address_slug(street[1], source.get('house_number') or ''), SHARED_SLUG: None}


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
        self.places = '{}-places'.format(prefix)
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
        """
        One street of each name in each place, and in each settlement of it.

        Returns ``(name, place slug) -> [(name, slug, line), ...]``: the
        streets an address naming one there is linked to, ``line`` where
        there is more than one to choose between by distance.
        """
        # where each street is and what it is called, and nothing more: a
        # country's streets run to a million and more
        groups = defaultdict(list)
        for hit in self.scan(self.streets, {'match_all': {}}, ['name', 'slug', 'ancestry']):
            place = last(hit['_source'].get('ancestry'), 'place')
            if place:
                groups[(hit['_source']['name'], place['slug'])].append(hit)
        settled = self.settlements_by_street()
        by_name, work = defaultdict(list), []
        for key, found in groups.items():
            if len(found) > 1 or len(settled.get(found[0]['_source']['slug'], ())) > 1:
                work.append(found)
            else:
                by_name[key].append((found[0]['_source']['name'], found[0]['_source']['slug'], None))

        streets, addresses = [], []
        self.given = set()
        counts = defaultdict(int)
        for start in range(0, len(work), 200):
            batch = work[start:start + 200]
            houses = self.houses_on([hit['_source']['slug'] for found in batch for hit in found])
            for found in batch:
                result = self.settle(found, houses, counts)
                streets.extend(result['streets'])
                addresses.extend(result['addresses'])
                for name, slug, place, line in result['made']:
                    by_name[(name, place)].append((name, slug, line))
        self.write(self.streets, streets)
        self.write(self.addresses, addresses)
        self.stdout.write(
            'streets: {joined} names joined from {pieces} more pieces, {split} split into '
            '{made} more by settlement; addresses: {moved} moved to their street'.format(
                joined=counts['joined'], pieces=counts['pieces'], split=counts['split'],
                made=counts['made'], moved=len(addresses)))
        return by_name

    def settlements_by_street(self):
        """``{street slug: {settlement, ...}}``, as the houses on each name them."""
        found = defaultdict(set)
        query = {'bool': {'filter': [
            {'exists': {'field': 'settlement_name'}},
            {'nested': {'path': 'ancestry', 'query': {'term': {'ancestry.level': 'street'}}}},
        ]}}
        for hit in self.scan(self.addresses, query, ['ancestry', 'settlement_name']):
            street = last(hit['_source'].get('ancestry'), 'street')
            key = settlement_key(hit['_source'].get('settlement_name'))
            if street and key:
                found[street['slug']].add(key)
        return found

    def houses_on(self, slugs):
        """``{street slug: [hit, ...]}``: the houses on those streets."""
        found = defaultdict(list)
        for start in range(0, len(slugs), 5000):
            query = {'nested': {'path': 'ancestry', 'query': {'bool': {'filter': [
                {'term': {'ancestry.level': 'street'}},
                {'terms': {'ancestry.slug': slugs[start:start + 5000]}}]}}}}
            for hit in self.scan(self.addresses, query,
                                 ['ancestry', 'house_number', 'center', 'settlement_name']):
                street = last(hit['_source'].get('ancestry'), 'street')
                if street:
                    found[street['slug']].append(hit)
        return found

    def settle(self, found, houses, counts):
        """
        The pieces of one name in one place as the streets they are.

        One street when their houses name one settlement, as they were
        joined before; one for each settlement when they name more, the
        longest keeping the shortest slug of the pieces, and another a slug
        of its own, as the recalculation qualifies one.
        """
        from shapely.geometry import shape
        from shapely.ops import unary_union

        docs = self.client.mget(body={'docs': [
            {'_index': hit['_index'], '_id': hit['_id']} for hit in found]})['docs']
        sources = [doc['_source'] for doc in docs]
        lines, owner = [], []
        for i, source in enumerate(sources):
            if source.get('geometry'):
                geometry = shape(source['geometry'])
                for line in getattr(geometry, 'geoms', [geometry]):
                    lines.append(line)
                    owner.append(i)
        on = [h for source in sources for h in houses.get(source['slug'], [])]
        points = [(h['_source']['center']['lon'], h['_source']['center']['lat'])
                  if h['_source'].get('center') else None for h in on]
        groups = streets(lines, [(p[0], p[1], h['_source'].get('settlement_name'))
                                 for p, h in zip(points, on) if p]) if lines else []
        result = {'streets': [], 'addresses': [], 'made': []}
        name = sources[0]['name']

        if len(groups) < 2:
            # one street: the pieces joined, as they were before settlements
            stays = 0
            if len(docs) > 1:
                stays, fields = joined_street(sources)
                result['streets'].append(update(docs[stays], fields))
                gone = {sources[i]['slug'] for i in range(len(docs)) if i != stays}
                result['streets'].extend(delete(doc) for i, doc in enumerate(docs) if i != stays)
                result['addresses'].extend(
                    update(h, with_street(h['_source'], (name, sources[stays]['slug'])))
                    for h in on if last(h['_source']['ancestry'], 'street')['slug'] in gone)
                counts['joined'] += 1
                counts['pieces'] += len(gone)
            place = last(sources[stays].get('ancestry'), 'place')
            result['made'].append((name, sources[stays]['slug'], place['slug'], None))
            return result

        # which document each street is: the one with the shortest slug of
        # those with a piece in it, the longest street first; a new one where
        # none is left
        used, chosen = set(), []
        for group in groups:
            present = sorted({owner[i] for i in group} - used,
                             key=lambda d: (len(sources[d]['slug']), sources[d]['slug']))
            chosen.append(present[0] if present else None)
            used.update(present[:1])
        where = nearest(lines, groups, [p or (0, 0) for p in points])
        counts['split'] += 1
        counts['made'] += sum(1 for d in chosen if d is None)
        for k, group in enumerate(groups):
            own = [h for h, g, p in zip(on, where, points) if g == k and p]
            template = chosen[k] if chosen[k] is not None else max(
                {owner[i] for i in group},
                key=lambda d: sum(lines[i].length for i in group if owner[i] == d))
            fields = self.street_fields(sources, lines, owner, group, template, own)
            if chosen[k] is None:
                doc = dict(sources[template], **fields)
                doc['slug'] = self.qualified_street_slug(doc)
                doc['osm'] = {'node_id': None, 'way_id': None, 'relation_id': None}
                pk = '{}-{}'.format(docs[template]['_id'], k)
                result['streets'].append({'_op_type': 'index', '_index': docs[template]['_index'],
                                          '_id': pk, '_source': doc})
                slug = doc['slug']
            else:
                gone = [sources[d] for d in {owner[i] for i in group} - used]
                fields['osm'] = merged_osm(sources[chosen[k]], gone)
                result['streets'].append(update(docs[chosen[k]], fields))
                slug = sources[chosen[k]]['slug']
            for h in own:
                if last(h['_source']['ancestry'], 'street')['slug'] != slug:
                    result['addresses'].append(update(h, with_street(h['_source'], (name, slug))))
            place = last(fields['ancestry'], 'place')
            line = unary_union([lines[i] for i in group])
            result['made'].append((name, slug, place['slug'] if place else None, line))
        result['streets'].extend(delete(docs[d]) for d in range(len(docs)) if d not in used)
        return result

    def street_fields(self, sources, lines, owner, group, template, houses):
        """
        What one street of several is: its pieces' line, and the ancestry of
        the settlement its houses name, as the recalculation gives a street
        its place from its houses.
        """
        from shapely.geometry import MultiLineString, mapping
        from shapely.ops import linemerge, unary_union

        union = unary_union([lines[i] for i in group])
        merged = linemerge(union) if union.geom_type != 'LineString' else union
        if merged.geom_type == 'LineString':
            merged = MultiLineString([merged])
        length = 0
        for d in {owner[i] for i in group}:
            mine = sum(line.length for line, o in zip(lines, owner) if o == d) or 1.0
            length += (sources[d].get('length') or 0) * sum(
                lines[i].length for i in group if owner[i] == d) / mine
        point = merged.representative_point()
        settlement = Counter(h['_source'].get('settlement_name') for h in houses
                             if settlement_key(h['_source'].get('settlement_name')))
        entries = sources[template].get('ancestry') or []
        place = settlement and self.place_named(settlement.most_common(1)[0][0], point)
        if place:
            entries = [e for e in place.get('ancestry') or []
                       if e.get('level') in ('country', 'countrysubarea')]
            entries.append({'level': 'place', 'slug': place['slug'], 'name': place['name']})
        else:
            entries = [e for e in entries if e.get('level') != 'placesubarea']
        districts = Counter((e['slug'], e['name']) for e in (
            last(h['_source'].get('ancestry'), 'placesubarea') for h in houses) if e)
        if districts:
            slug, district = districts.most_common(1)[0][0]
            entries.append({'level': 'placesubarea', 'slug': slug, 'name': district})
        entries, slugs, names = flatten_ancestry(entries)
        return {'geometry': mapping(merged), 'length': length,
                'center': {'lon': point.x, 'lat': point.y}, 'bbox': list(merged.bounds),
                'ancestry': entries, 'ancestry_slugs': slugs, 'ancestry_names': names,
                'place_names': place_names(entries)}

    def place_named(self, settlement, point):
        """The place of that name nearest ``point``, as a street's houses' settlement is found."""
        hits = self.client.search(index=self.places, body={
            'size': 1, '_source': ['name', 'slug', 'ancestry'],
            'query': {'term': {'name.keyword': settlement}},
            'sort': [{'_geo_distance': {'center': {'lon': point.x, 'lat': point.y},
                                        'order': 'asc'}}]})['hits']['hits']
        return hits[0]['_source'] if hits else None

    def qualified_street_slug(self, doc):
        """
        A slug for a street that was a piece of another, as the recalculation
        qualifies a namesake: by its place, its district, its region, and its
        place in the pack when none of those is free.
        """
        base = slugify(doc['name'])[:SLUG_LENGTH]
        candidates = [qualified(base, slugify(e['slug']))
                      for level in ('place', 'placesubarea', 'countrysubarea')
                      for e in [first(doc['ancestry'], level)] if e and e['slug'] != base]
        held = {hit['_source']['slug'] for hit in self.client.search(index=self.streets, body={
            'size': len(candidates), '_source': ['slug'],
            'query': {'terms': {'slug': candidates}}})['hits']['hits']} if candidates else set()
        for candidate in candidates:
            if candidate not in held and candidate not in self.given:
                self.given.add(candidate)
                return candidate
        n = 2
        while qualified(base, n) in self.given or self.client.count(index=self.streets, body={
                'query': {'term': {'slug': qualified(base, n)}}})['count']:
            n += 1
        self.given.add(qualified(base, n))
        return qualified(base, n)

    def link_addresses(self, by_name):
        from shapely.geometry import Point

        actions, total = [], 0
        for hit in self.scan(self.addresses, UNLINKED,
                             ['ancestry', 'house_number', 'street_name', 'center']):
            total += 1
            place = last(hit['_source'].get('ancestry'), 'place')
            candidates = place and by_name.get((hit['_source']['street_name'], place['slug']))
            if not candidates:
                continue
            street = candidates[0]
            center = hit['_source'].get('center')
            if len(candidates) > 1 and center:
                # the nearest of the streets of that name there
                point = Point(center['lon'], center['lat'])
                street = min(candidates, key=lambda c: c[2].distance(point) if c[2] else 0)
            actions.append(update(hit, with_street(hit['_source'], street[:2])))
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

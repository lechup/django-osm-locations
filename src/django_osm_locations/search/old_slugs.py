"""
The slug a location has now, for the one it had before slugs were qualified
by place.

Every duplicate but the biggest used to get its OSM id appended --
``slug || '-' || COALESCE(relation_osm_id, way_osm_id, node_osm_id, id)``,
``station-road-25986049`` -- and links to those are out there, in search
engines among other places. The id is still on the location, or on the one
it was merged into (``osm.merged_*_ids``), so the old slug is followed by
it. The name before the id says which, where several objects share the
number: OSM numbers nodes, ways and relations separately. It is compared
loosely, since the names were transliterated differently then -- German
umlauts as "ue".

Read from the indexes, like the API, so it works with nothing but packs.
"""
import hashlib
import re
from difflib import SequenceMatcher

from django.core.cache import cache

from ..conf import get_old_slug_cache_timeout
from ..utils.slugify import slugify
from .client import get_client, get_index_prefix
from .indexes import INDEXES

#: ``<slug>-<number>``: the shape of an old slug. Today's slugs can have it
#: too -- aleja-3-maja, a house number -- so a slug that exists is never
#: taken for an old one.
OLD_SLUG = re.compile(r'^(?P<name>.+)-(?P<number>\d+)$')

#: Where the number can be, in the order the old COALESCE took them, and the
#: location's own ids before those of what was merged into it.
ID_FIELDS = ('relation_id', 'way_id', 'node_id',
             'merged_relation_ids', 'merged_way_ids', 'merged_node_ids')

#: How alike the name before the number and the location's name must be.
#: Loose with the id to go by; close without, when the name is all there is.
SAME_NAME = 0.75
SAME_NAME_ALONE = 0.9

LONG_MAX = 2 ** 63 - 1


def resolve_old_slug(slug, types=None, within=None, client=None):
    """
    The slug ``slug`` is now, or None when it is current or cannot be told.

    ``types`` limits the location types. ``within`` is an ancestor's slug --
    a place subdomain's: an old slug whose number is nowhere in the data any
    more, the object deleted from OSM, is looked for by its name under it,
    and followed when exactly one location fits.
    """
    if not slug:
        return None
    key = 'django-osm-locations:old-slug:' + hashlib.md5('{}|{}|{}'.format(
        ','.join(sorted(types or [])), within or '', slug).encode('utf-8')).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    found = _resolve(slug, types, within, client or get_client())
    cache.set(key, found or '', get_old_slug_cache_timeout())
    return found


def _resolve(slug, types, within, client):
    match = OLD_SLUG.match(slug)
    if not match:
        return None
    prefix = get_index_prefix()
    index = ','.join(INDEXES[name].alias(prefix) for name in (types or INDEXES))
    filters = [{'term': {'ancestry_slugs': within}}] if within else []

    def search(should, scoped=True):
        return client.search(index=index, ignore_unavailable=True, body={
            'size': 50, '_source': ['slug', 'name', 'osm'],
            'query': {'bool': {'should': should, 'minimum_should_match': 1,
                               'filter': filters if scoped else []}},
        })['hits']['hits']

    # a slug of today's anywhere is not an old one, whatever its shape
    if search([{'term': {'slug': slug}}], scoped=False):
        return None
    name, number = match.group('name'), int(match.group('number'))
    if number <= LONG_MAX:
        # wherever it is now: the id says which, and the street may have
        # changed place since
        hits = search([{'term': {'osm.{}'.format(field): number}} for field in ID_FIELDS],
                      scoped=False)
        chosen = _best(hits, name, number, SAME_NAME)
        if chosen:
            return chosen
    if within:
        hits = search([{'term': {'slug': name}}, {'prefix': {'slug': name + '-'}}])
        named = [h for h in hits if _alike(h['_source'].get('name'), name) >= SAME_NAME_ALONE]
        if len(named) == 1:
            return named[0]['_source']['slug']
    return None


def _alike(location_name, old_name):
    return SequenceMatcher(None, slugify(location_name or ''), old_name).ratio()


def _holds(value, number):
    return number in value if isinstance(value, list) else value == number


def _best(hits, name, number, threshold):
    """The location the number and the name agree on, or None when two do equally."""
    ranked = []
    for hit in hits:
        osm = hit['_source'].get('osm') or {}
        where = next((i for i, field in enumerate(ID_FIELDS) if _holds(osm.get(field), number)),
                     None)
        alike = _alike(hit['_source'].get('name'), name)
        if where is not None and alike >= threshold:
            ranked.append((where, -alike, hit['_source']['slug']))
    ranked.sort()
    if not ranked or (len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]
                      and ranked[0][2] != ranked[1][2]):
        return None
    return ranked[0][2]

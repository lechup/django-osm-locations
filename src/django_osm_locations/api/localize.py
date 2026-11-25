"""
Serving names in the language the caller asked for.

OSM carries translations as ``name:<lang>``, and coverage runs opposite to row
count: a country relation has 200-270 of them, a voivodeship about 95, while
7-10% of places and 0.5% of streets have any at all. So the rows people most
want translated are the ones that carry translations.

Ancestry is the awkward part. A hit's own name is a field lookup, but
"Wielka Brytania > Anglia > Birmingham" needs its ancestors translated too,
and those live in other documents. Storing each ancestor's translations on
every descendant would put roughly four ancestors times 250 languages on each
of 1.86 million streets -- far more than the geometry we already work to keep
down. Resolving them per response instead costs one lookup for the distinct
ancestors on a page, which for twenty hits is a couple of dozen slugs, and
they repeat heavily from page to page.
"""
from django.core.cache import cache

from ..conf import ancestor_names_cache_timeout
from ..search.indexes import INDEXES


def localized_name(source, language):
    """A document's own name in ``language``, falling back to what OSM called it."""
    if not language:
        return source.get('name')
    return (source.get('names') or {}).get(language) or source.get('name')


def ancestor_keys(hits):
    """
    (type, slug) for every ancestor mentioned on this page, deduplicated.

    Ancestors repeat across hits -- twenty streets in one town share its
    place, subarea and country -- so the set is far smaller than the hits.
    """
    keys = set()
    for hit in hits:
        for entry in hit.get('ancestry') or []:
            index = LEVEL_TO_INDEX.get(entry.get('level'))
            if index and entry.get('slug'):
                keys.add((index, entry['slug']))
    return keys


#: `level` as stored on an ancestry entry -> the index holding that location.
LEVEL_TO_INDEX = {
    'country': 'countries',
    'countrysubarea': 'country-subareas',
    'place': 'places',
    'placesubarea': 'place-subareas',
    'street': 'streets',
}


def fetch_ancestor_names(client, keys, language, prefix=None):
    """
    ``{(index, slug): translated name}`` for the ancestors on one page.

    One terms query per index rather than one per ancestor: a page of twenty
    street hits usually touches three or four indexes and a couple of dozen
    slugs.

    And remembered, found or not. Only the names are asked for, but a
    document is read whole to give them, and an ancestor's holds its border:
    Poland's is megabytes, and it is an ancestor of every hit in the country.
    Asked each time, ``lang=pl`` took the median suggestion from 10 ms to
    190 ms. The same few ancestors come back page after page, so after the
    first they cost nothing; a newer pack's names are seen once the entry
    expires, ``LOCATIONS_ANCESTOR_NAMES_CACHE_TIMEOUT``.
    """
    if not keys or not language:
        return {}

    timeout = ancestor_names_cache_timeout()
    found, by_index = {}, {}
    cache_keys = {key: _cache_key(prefix, language, *key) for key in keys} if timeout else {}
    cached = cache.get_many(list(cache_keys.values())) if cache_keys else {}
    for key in keys:
        if cache_keys.get(key) in cached:
            if cached[cache_keys[key]]:
                found[key] = cached[cache_keys[key]]
            continue
        by_index.setdefault(key[0], []).append(key[1])

    fresh = {}
    for index, slugs in by_index.items():
        try:
            response = client.search(
                index=INDEXES[index].alias(prefix),
                ignore_unavailable=True,
                body={
                    'size': len(slugs),
                    'query': {'terms': {'slug': slugs}},
                    '_source': ['slug', 'names'],
                },
            )
        except Exception:
            # a missing index must not take the whole response down; the
            # caller falls back to the untranslated name -- and asks again
            # next time, so nothing of this index is remembered
            continue
        # an ancestor with no translation is remembered as that
        fresh.update({(index, slug): '' for slug in slugs})
        for hit in response.get('hits', {}).get('hits', []):
            source = hit.get('_source', {})
            translated = (source.get('names') or {}).get(language)
            if translated:
                fresh[(index, source.get('slug'))] = translated
    if timeout and fresh:
        cache.set_many({cache_keys[key]: name for key, name in fresh.items() if key in cache_keys},
                       timeout)
    found.update({key: name for key, name in fresh.items() if name})
    return found


def _cache_key(prefix, language, index, slug):
    """A cache key for one ancestor's name: no spaces, and short enough for memcached."""
    import hashlib

    raw = '{}|{}|{}|{}'.format(prefix or '', language, index, slug)
    return 'locations-ancestor-name:' + hashlib.sha1(raw.encode('utf-8')).hexdigest()


def apply_language(hits, language, ancestor_names):
    """Rewrite each hit's own name and its ancestry into ``language``."""
    if not language:
        return hits
    for hit in hits:
        if hit.get('type') != 'addresses':
            # An address is named for its street and number, and the
            # translations it carries are those of whatever was mapped at
            # it: with lang=pl "Marszałkowska 81" came back as "Żabka", the
            # shop's name:pl, and "Floriańska 38" as "Hotel Floryan".
            hit['name'] = localized_name(hit, language)
        entries = hit.get('ancestry') or []
        for entry in entries:
            index = LEVEL_TO_INDEX.get(entry.get('level'))
            translated = ancestor_names.get((index, entry.get('slug')))
            if translated:
                entry['name'] = translated
        if entries:
            # kept in step with `ancestry`; slugs stay as they are, being
            # identifiers rather than labels
            hit['ancestry_names'] = [entry.get('name') for entry in entries]
    return hits

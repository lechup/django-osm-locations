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
    """
    if not keys or not language:
        return {}

    by_index = {}
    for index, slug in keys:
        by_index.setdefault(index, []).append(slug)

    found = {}
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
            # caller falls back to the untranslated name
            continue
        for hit in response.get('hits', {}).get('hits', []):
            source = hit.get('_source', {})
            translated = (source.get('names') or {}).get(language)
            if translated:
                found[(index, source.get('slug'))] = translated
    return found


def apply_language(hits, language, ancestor_names):
    """Rewrite each hit's own name and its ancestry into ``language``."""
    if not language:
        return hits
    for hit in hits:
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

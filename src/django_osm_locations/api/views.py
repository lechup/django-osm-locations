"""
Read-only API served from OpenSearch.

PostGIS remains the source of truth, but nothing here queries it: the index
carries the geometry as well as the searchable fields, so a request is one
OpenSearch round trip.
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..search.client import get_client, get_index_prefix
from ..search.indexes import INDEXES
from ..utils.shared_slugs import SHARED_SLUG
from .localize import ancestor_keys, apply_language, fetch_ancestor_names
from .queries import (
    QueryError, build_body, exclude_types, found_as_typed, fuzzy_words, parse_fields, parse_types,
    parse_within, read_query, without_addresses_too_early,
)


def add_path(hit):
    """
    The hit's ancestry and its own name as one readable path.

    ancestry keeps every level separately, which is what filtering needs -- a
    city with county rights is a county, a commune and a place, three entities
    with three slugs that all happen to be called Żory. Printed as they are
    that reads "Polska › śląskie › Żory › Żory › Żory › Rogoźna", so the path
    collapses a name repeated by the next level. Only the display: ancestry
    and ancestry_slugs are left exactly as they were.
    """
    path = []
    for name in [entry.get('name') for entry in hit.get('ancestry') or []] + [hit.get('name')]:
        if name and (not path or path[-1] != name):
            path.append(name)
    hit['path'] = path
    return hit


def format_hit(hit):
    """
    One OpenSearch hit as an API result.

    Without ``id``: it is the row's primary key in the database the country
    was built in, and each country is built in a database of its own, so two
    countries hand out the same ids. A location is identified by its type, its
    country and its slug -- unique together, and what the detail endpoint
    looks it up by. The index keeps ``id`` as the document key.
    """
    source = dict(hit.get('_source') or {})
    source.pop('id', None)
    source['score'] = hit.get('_score')
    return source


def add_country(hit):
    """
    The slug of the country a hit is in: with its type and slug, what names it.

    Its own slug for a country. Asked for with ``fields``, where the ancestry
    it is read from is usually left out.
    """
    if hit.get('type') == 'countries':
        hit['country'] = hit.get('slug')
        return hit
    hit['country'] = next((entry.get('slug') for entry in hit.get('ancestry') or []
                           if entry.get('level') == 'country'), None)
    return hit


def only_fields(hit, fields):
    """``hit`` with the fields asked for, in that order."""
    return {name: hit.get(name) for name in fields}


def total_of(result):
    total = result.get('hits', {}).get('total', {})
    return total.get('value', 0) if isinstance(total, dict) else int(total or 0)


def format_response(result, page, page_size):
    hits = result.get('hits', {}).get('hits', [])
    payload = {
        'total': total_of(result),
        'page': page,
        'page_size': page_size,
        'results': [format_hit(hit) for hit in hits],
    }
    if result.get('timed_out'):
        # what was found in the time allowed, and not all there is
        payload['timed_out'] = True
    return payload


#: With this many hits or fewer, the strict stage is followed by the one for
#: typos. None is the plain case -- "Warszwa" is the start of nothing -- but a
#: misspelt name is often the start of some other: "Szczecn" of the village
#: Szczecno and so of the eleven things in it, "Hambrug" of a street called
#: Hambrüggeweg, and that was all the answer there was.
FEW_HITS = 20


class SearchView(APIView):
    """
    Search across every location type, or a subset of them.

    Parameters:

    * ``q`` -- free text, prefix matched against the name and ancestry. When
      few results have every word as typed, and the best of them only as
      the start of another word, those that have a word misspelt are ranked
      among them -- "Warszwa" finds Warszawa -- addresses aside. With
      one or two letters and no digit, addresses are not searched unless
      they are all that is asked for.
    * ``type`` -- comma separated: countries, country-subareas, places,
      place-subareas, streets, addresses
    * ``exclude_type`` -- comma separated, the types to leave out:
      ``exclude_type=addresses`` suggests streets and places while a name is
      being typed, and a street's houses are then
      ``/addresses/?within=<street slug>&q=<number>``
    * ``within`` -- comma separated ancestor slugs, e.g. ``within=douglas``;
      ``places:antwerpen`` for the city alone, where the province around it
      has the same slug
    * ``bbox`` -- ``minlon,minlat,maxlon,maxlat``
    * ``near`` + ``radius`` -- ``near=-4.48,54.15&radius=5km``
    * ``prefer`` -- ``prefer=19.94,50.06``: a point, lon,lat, the results
      nearer to which rank higher. Nothing is left out for being far, as
      ``near`` leaves it: of two streets of one name the nearer comes first,
      and a city typed from the other end of the country is still the city.
    * ``fields`` -- ``fields=suggest`` answers with type, slug, name, path,
      country, center, bbox and score, an eighth of a whole result; or a
      list of the fields wanted, ``fields=name,slug,center``
    * ``order`` -- ``relevance`` (default with ``q``), ``name``, ``size``, or
      ``number`` -- house numbers in walking order, 1, 2, 3, 3 B, 10, 12a;
      the default for a request that is only addresses
    * ``geometry=true`` -- include the full geometry in each result
    * ``lang`` -- return names in this language where OSM has one, e.g.
      ``lang=pl`` for Niemcy rather than Deutschland. Ancestry is translated
      too, and anything untranslated keeps the name OSM stored.
    * ``page``, ``page_size``
    """

    location_types = None

    def get(self, request):
        params = request.query_params
        try:
            names = exclude_types(self.location_types or parse_types(params.get('type')),
                                  params.get('exclude_type'))
            names = without_addresses_too_early(names, params.get('q'))
            body, page, page_size = build_body(params, names)
        except QueryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        prefix = get_index_prefix()
        aliases = [INDEXES[name].alias(prefix) for name in names]

        search_kwargs = {}
        if len(aliases) > 1:
            # Each index computes term statistics over its own documents, so a
            # score from the places index is not comparable with one from the
            # much larger streets index -- searching "peel" returned five Peel
            # Roads and not the town. dfs_query_then_fetch gathers global term
            # statistics first, at the cost of one extra round trip. Only worth
            # it when more than one index is involved.
            search_kwargs['search_type'] = 'dfs_query_then_fetch'

        client = get_client()
        result = client.search(
            index=','.join(aliases),
            body=body,
            ignore_unavailable=True,
            **search_kwargs
        )
        relaxed = False
        if params.get('q') and not total_of(result) and read_query(params['q']).relaxed:
            # nothing has Osiedle, Aleja or Plac with the rest: without it
            relaxed = True
            body, page, page_size = build_body(params, names, relaxed=True)
            result = client.search(
                index=','.join(aliases),
                body=body,
                ignore_unavailable=True,
                **search_kwargs
            )
        result = self.with_typos(client, result, params, names, aliases, page, page_size,
                                 search_kwargs, relaxed)
        if params.get('q') and not result.get('hits', {}).get('hits') and page == 1:
            # nothing has every word: a typo, or a word the data does not
            # know -- answer with what has any of them, as before
            body, page, page_size = build_body(params, names, strict=False)
            result = client.search(
                index=','.join(aliases),
                body=body,
                ignore_unavailable=True,
                **search_kwargs
            )
        payload = format_response(result, page, page_size)

        language = (params.get('lang') or '').strip()
        if language:
            hits = payload.get('results', [])
            apply_language(
                hits, language,
                fetch_ancestor_names(client, ancestor_keys(hits), language, prefix),
            )
        # after the language, so the path is in it too
        for hit in payload.get('results', []):
            add_path(hit)
        fields = parse_fields(params.get('fields'))
        if fields is not None:
            payload['results'] = [only_fields(add_country(hit), fields)
                                  for hit in payload['results']]
        return Response(payload)


    @staticmethod
    def with_typos(client, result, params, names, aliases, page, page_size, search_kwargs,
                   relaxed=False):
        """
        ``result``, or the answer of the typo stage where that has more.

        Asked only when few have every word as typed, and the first of those
        does not have them whole: "Marszałkowska Warszawa" is one street and
        no typo, and is not searched twice.
        """
        q = params.get('q')
        if not q or total_of(result) > FEW_HITS or names == ['addresses'] or not fuzzy_words(q):
            return result
        first = result
        if page > 1 and total_of(result):
            # the same decision on every page: by the first hit of the first
            body, _, _ = build_body(dict(params.items(), page=1, page_size=1), names,
                                    relaxed=relaxed)
            first = client.search(index=','.join(aliases), body=body,
                                  ignore_unavailable=True, **search_kwargs)
        top = first.get('hits', {}).get('hits') or []
        if top and found_as_typed(q, top[0].get('_source') or {}):
            return result
        body, _, _ = build_body(params, names, fuzzy=True, relaxed=True)
        found = client.search(index=','.join(aliases), body=body,
                              ignore_unavailable=True, **search_kwargs)
        return found if total_of(found) > total_of(result) else result


def type_view(name):
    """A SearchView pinned to one location type."""
    return type(
        '{}SearchView'.format(name.title().replace('-', '')),
        (SearchView,),
        {'location_types': [name], '__doc__': SearchView.__doc__},
    )


class DetailView(APIView):
    """
    One location by slug, with its full geometry.

    Slugs are unique per type *within a country*, not across the world. That
    is what lets each country's index be built and published on its own --
    adding Denmark cannot rename a Polish street -- and it means that with
    several countries loaded, a slug can name more than one place. Measured
    across Poland, Britain and Denmark that is 60 slugs: mostly cycle trails
    called "jurassic-park" and short Danish-English villages like "dale".

    Where it happens, say so rather than pick one. ``?within=<ancestor slug>``
    narrows it, the same parameter search uses.

    Parameters:

    * ``within`` -- an ancestor slug, e.g. ``within=polska``, to choose
      between countries when a slug is ambiguous; ``places:antwerpen`` for
      the city when the province has its slug too
    * ``lang`` -- return names in this language where OSM has one
    """

    #: Enough to tell the caller which is which without fetching everything.
    AMBIGUITY_LIMIT = 10

    def get(self, request, location_type, slug):
        if location_type not in INDEXES:
            return Response(
                {'detail': 'Unknown type: {}. Known types: {}'.format(
                    location_type, ', '.join(sorted(INDEXES)))},
                status=status.HTTP_404_NOT_FOUND,
            )

        alias = INDEXES[location_type].alias(get_index_prefix())
        try:
            within = parse_within(request.query_params.get('within'))
        except QueryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        must = [{'term': {'slug': slug}}] + within

        client = get_client()
        result = client.search(
            index=alias,
            # one more than we can serve, so more than one is visible rather
            # than silently truncated to the first
            body={'query': {'bool': {'filter': must}}, 'size': self.AMBIGUITY_LIMIT},
            ignore_unavailable=True,
        )
        hits = result.get('hits', {}).get('hits', [])
        if not hits and location_type == 'addresses':
            # houses that shared this slug and have one each now: named, as
            # they were when they shared it -- see utils.shared_slugs
            hits = client.search(
                index=alias,
                body={'query': {'bool': {'filter': [{'term': {SHARED_SLUG: slug}}] + within}},
                      'size': self.AMBIGUITY_LIMIT},
                ignore_unavailable=True,
            ).get('hits', {}).get('hits', [])
        if not hits:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if len(hits) > 1:
            return self._ambiguous(slug, hits)

        payload = format_hit(hits[0])
        language = (request.query_params.get('lang') or '').strip()
        if language:
            apply_language(
                [payload], language,
                fetch_ancestor_names(
                    client, ancestor_keys([payload]), language, get_index_prefix(),
                ),
            )
        return Response(add_path(payload))

    def _ambiguous(self, slug, hits):
        """
        Several rows answer to this slug, so name them instead of guessing.

        Taking the first was what this did, and with one country loaded it was
        safe because slugs were unique. Across countries the winner depends on
        the order shards come back in, which is not a decision anyone made.
        """
        candidates = []
        for hit in hits:
            source = hit.get('_source') or {}
            candidates.append({
                'slug': source.get('slug'),
                'name': source.get('name'),
                'ancestry_slugs': source.get('ancestry_slugs') or [],
            })
        if all(candidate['slug'] != slug for candidate in candidates):
            detail = ('{} houses shared the slug {!r}, and each has one of its own now: '
                      'see the candidates.'.format(len(hits), slug))
        else:
            detail = ('{} locations share the slug {!r}. A slug is unique within '
                      'a country, not across them; add ?within=<ancestor slug> '
                      'to choose.'.format(len(hits), slug))
        return Response({'detail': detail, 'candidates': candidates},
                        status=status.HTTP_409_CONFLICT)

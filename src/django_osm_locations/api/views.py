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
from .queries import QueryError, build_body, exclude_types, parse_types, parse_within


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


def format_response(result, page, page_size):
    total = result.get('hits', {}).get('total', {})
    total = total.get('value', 0) if isinstance(total, dict) else int(total or 0)
    hits = result.get('hits', {}).get('hits', [])
    return {
        'total': total,
        'page': page,
        'page_size': page_size,
        'results': [format_hit(hit) for hit in hits],
    }


class SearchView(APIView):
    """
    Search across every location type, or a subset of them.

    Parameters:

    * ``q`` -- free text, prefix matched against the name and ancestry
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
        return Response(payload)


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

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
from .localize import ancestor_keys, apply_language, fetch_ancestor_names
from .queries import QueryError, build_body, parse_types


def format_hit(hit):
    """One OpenSearch hit as an API result."""
    source = dict(hit.get('_source') or {})
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
      place-subareas, streets
    * ``within`` -- comma separated ancestor slugs, e.g. ``within=douglas``
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
            names = self.location_types or parse_types(params.get('type'))
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
        payload = format_response(result, page, page_size)

        language = (params.get('lang') or '').strip()
        if language:
            hits = payload.get('results', [])
            apply_language(
                hits, language,
                fetch_ancestor_names(client, ancestor_keys(hits), language, prefix),
            )
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

    Slugs are unique per type, so the type is part of the URL.
    """

    def get(self, request, location_type, slug):
        if location_type not in INDEXES:
            return Response(
                {'detail': 'Unknown type: {}. Known types: {}'.format(
                    location_type, ', '.join(sorted(INDEXES)))},
                status=status.HTTP_404_NOT_FOUND,
            )

        alias = INDEXES[location_type].alias(get_index_prefix())
        client = get_client()
        result = client.search(
            index=alias,
            body={'query': {'term': {'slug': slug}}, 'size': 1},
            ignore_unavailable=True,
        )
        hits = result.get('hits', {}).get('hits', [])
        if not hits:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(format_hit(hits[0]))

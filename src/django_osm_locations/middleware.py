from django.conf import settings
from django.http import Http404, HttpResponsePermanentRedirect
from django.shortcuts import get_object_or_404
from django.utils.encoding import iri_to_uri

from .conf import get_reserved_subdomains, old_slug_redirects
from .models import Place


class PlaceSubdomainMiddleware:
    """
    Resolve ``<place-slug>.example.com`` to a ``Place``.

    Sets on every request:

    * ``request.subdomain`` -- the slug, ``None`` on the apex domain (and on
      reserved subdomains such as ``www``), or ``False`` for a host that does
      not belong to ``settings.ROOT_URL`` at all.
    * ``request.place`` -- the matching ``Place``, or ``None``. Always set, so
      views can read it unconditionally.

    ``ROOT_URL`` drives the parsing, so local development works by pointing it
    at the development host, e.g. ``ROOT_URL = 'localhost:8000'`` makes both
    ``localhost:8000`` and ``krakow.localhost:8000`` behave correctly.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.subdomain = self.get_subdomain_from_host(request)
        # Set unconditionally: views on the apex domain used to raise
        # AttributeError because this only existed on the subdomain branch.
        request.place = None
        request.META['HTTP_X_SUBDOMAIN'] = request.subdomain or ''

        if request.subdomain is False:
            return HttpResponsePermanentRedirect('{}://{}'.format(
                self.get_protocol(request),
                settings.ROOT_URL,
            ))

        if request.subdomain:
            redirect = self._resolve_place(request)
            if redirect is not None:
                return redirect

        response = self.get_response(request)
        if request.subdomain:
            response['X-Subdomain'] = request.subdomain
        return response

    def _resolve_place(self, request):
        """
        Attach the place for ``request.subdomain``.

        Returns a redirect response when the subdomain is a suffixed variant of
        a real place (``krakow-2`` -> ``krakow``), otherwise ``None``. Raises
        ``Http404`` when nothing matches.
        """
        try:
            request.place = get_object_or_404(Place.objects.all(), slug=request.subdomain)
        except Http404:
            if old_slug_redirects():
                # a place's old slug, wieliczka-<OSM id>, is that place first:
                # cutting the id off finds whichever Wieliczka has the bare slug
                from .search.old_slugs import resolve_old_slug

                new = resolve_old_slug(request.subdomain, types=['places'])
                if new:
                    return HttpResponsePermanentRedirect('{}://{}.{}{}'.format(
                        self.get_protocol(request), new, settings.ROOT_URL,
                        request.get_full_path()))
            parts = request.subdomain.split('-')
            if len(parts) == 2 and parts[0]:
                place = Place.objects.filter(slug=parts[0]).first()
                if place is not None:
                    request.place = place
                    return HttpResponsePermanentRedirect('{}://{}.{}{}'.format(
                        self.get_protocol(request),
                        parts[0],
                        settings.ROOT_URL,
                        request.get_full_path(),
                    ))
            raise
        return None

    @staticmethod
    def get_subdomain_from_host(request):
        """
        Split the host against ``ROOT_URL`` instead of counting dots.

        Counting dots misread every host whose shape was not
        ``sub.domain.tld``: ``www`` became a place slug, ``example.co.uk`` was
        read as the subdomain ``example``, and single-label hosts such as
        ``localhost`` were redirected away.
        """
        host = request.get_host().split(':')[0].strip('.').lower()
        root = str(getattr(settings, 'ROOT_URL', '') or '').split(':')[0].strip('.').lower()

        if not root or host == root:
            return None

        suffix = '.' + root
        if not host.endswith(suffix):
            return False

        subdomain = host[:-len(suffix)]
        if not subdomain or '.' in subdomain:
            # deeper nesting (a.b.example.com) is not a place
            return False
        if subdomain in get_reserved_subdomains():
            return None
        return subdomain

    @classmethod
    def get_protocol(cls, request):
        return 'https' if request.is_secure() else 'http'


class OldSlugRedirectMiddleware:
    """
    301 from an old slug to the one the location has now.

    Before slugs were qualified by place, a duplicate's slug ended in its OSM
    id -- ``station-road-25986049`` -- and those links are in search engines'
    indexes. Optional: add it to ``MIDDLEWARE`` before PlaceSubdomainMiddleware,
    so the 404 a place subdomain raises reaches it, and that middleware then
    also asks it about an old place slug before cutting the id off.

    Only a 404 is looked at, so a request that finds its page costs nothing.
    Every segment of the path shaped like ``<slug>-<number>`` that is no
    slug of today's is resolved -- whatever the project's URLs are -- under
    the place of the subdomain, if there is one; the request is redirected
    when any of them resolved. See search.old_slugs.resolve_old_slug.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code != 404:
            return response
        from .search.old_slugs import OLD_SLUG, resolve_old_slug

        within = getattr(request, 'subdomain', None) or None
        segments = request.path.split('/')
        changed = False
        for i, segment in enumerate(segments):
            if OLD_SLUG.match(segment):
                new = resolve_old_slug(segment, within=within)
                if new:
                    segments[i], changed = new, True
        if not changed:
            return response
        url = iri_to_uri('/'.join(segments))
        query = request.META.get('QUERY_STRING')
        return HttpResponsePermanentRedirect(url + ('?' + query if query else ''))

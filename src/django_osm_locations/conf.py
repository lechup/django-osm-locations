"""
Settings this app reads, with sensible fallbacks.

Everything is optional: the app works unconfigured, but once it is installed
from PyPI its own package directory is inside site-packages and is the wrong
place to write multi-gigabyte OSM extracts or cache files. Point these at a
writable location in your project instead.
"""
import os

from django.conf import settings


def _project_dir():
    """
    Best-effort writable base directory.

    ``BASE_DIR`` is a convention from ``django-admin startproject``, not part of
    Django's own settings, so it may legitimately be missing.
    """
    base_dir = getattr(settings, 'BASE_DIR', None)
    return str(base_dir) if base_dir else os.getcwd()


def get_data_dir():
    """
    Where OSM ``.pbf`` extracts are downloaded to and read from.

    Set ``LOCATIONS_DATA_DIR`` in settings. Defaults to ``<BASE_DIR>/osm_data``.
    """
    return str(getattr(settings, 'LOCATIONS_DATA_DIR', None) or os.path.join(_project_dir(), 'osm_data'))


def get_cache_dir():
    """
    Where the importer's node/way/relation caches are written.

    Set ``LOCATIONS_CACHE_DIR`` in settings. Defaults to ``<BASE_DIR>``.
    """
    return str(getattr(settings, 'LOCATIONS_CACHE_DIR', None) or _project_dir())


#: Where OldSlugRedirectMiddleware is to be found in ``MIDDLEWARE``.
OLD_SLUG_MIDDLEWARE = 'django_osm_locations.middleware.OldSlugRedirectMiddleware'


def old_slug_redirects():
    """Whether old slugs are redirected: OldSlugRedirectMiddleware is installed."""
    return OLD_SLUG_MIDDLEWARE in (getattr(settings, 'MIDDLEWARE', None) or ())


def get_old_slug_cache_timeout():
    """
    How long, in seconds, an old slug's answer is kept, found or not.

    Set ``LOCATIONS_OLD_SLUG_CACHE_TIMEOUT``. A day by default: crawlers ask
    the same old links again and again, and the data changes with a release.
    """
    return int(getattr(settings, 'LOCATIONS_OLD_SLUG_CACHE_TIMEOUT', 24 * 3600))


def get_reserved_subdomains():
    """
    Subdomains that are never treated as a place slug.

    Set ``LOCATIONS_RESERVED_SUBDOMAINS`` in settings to override. Without this,
    ``www.example.com`` is looked up as a place called "www" and 404s.
    """
    reserved = getattr(settings, 'LOCATIONS_RESERVED_SUBDOMAINS', None)
    if reserved is None:
        reserved = {'www', 'api', 'admin', 'static', 'media', 'mail'}
    return {str(name).lower() for name in reserved}

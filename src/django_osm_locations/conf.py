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

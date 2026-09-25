"""
Settings for running real imports against the local stack.

    docker compose -f docker-compose.local.yml up -d
    DJANGO_SETTINGS_MODULE=local.settings python -m django migrate
    DJANGO_SETTINGS_MODULE=local.settings python -m django locations_import poland

This is a development harness, not a deployment example: the credentials are
the ones in docker-compose.local.yml and the secret key is a placeholder.
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = 'local-development-only'
# Off by default. With DEBUG on, Django keeps the last 9000 statements it runs,
# fully interpolated: 0.6MB for one batch of streets, 4.9GB for a full deque,
# which is what made a Poland import grow 3.5GB and get stopped 45 minutes in.
# The management commands defend themselves now, but the demo server does not
# need it either. LOCATIONS_DEBUG=1 turns it back on.
DEBUG = os.environ.get('LOCATIONS_DEBUG') == '1'
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'django.contrib.gis',
    'django.contrib.postgres',
    'django_osm_locations',
    'rest_framework',
]

DATABASES = {
    'default': {
        'ENGINE': 'django.contrib.gis.db.backends.postgis',
        'NAME': os.environ.get('LOCATIONS_LOCAL_DB_NAME', 'locations'),
        'USER': os.environ.get('LOCATIONS_LOCAL_DB_USER', 'locations'),
        'PASSWORD': os.environ.get('LOCATIONS_LOCAL_DB_PASSWORD', 'locations'),
        'HOST': os.environ.get('LOCATIONS_LOCAL_DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('LOCATIONS_LOCAL_DB_PORT', '55433'),
    }
}

LOCATIONS_OPENSEARCH_HOSTS = [
    'http://%s:%s' % (
        os.environ.get('LOCATIONS_LOCAL_OPENSEARCH_HOST', '127.0.0.1'),
        os.environ.get('LOCATIONS_LOCAL_OPENSEARCH_PORT', '59201'),
    )
]
LOCATIONS_OPENSEARCH_INDEX_PREFIX = 'locations'

# Extracts land here rather than inside the installed package.
LOCATIONS_DATA_DIR = os.environ.get(
    'LOCATIONS_DATA_DIR', os.path.join(BASE_DIR, 'local', 'osm_data')
)
LOCATIONS_CACHE_DIR = os.path.join(BASE_DIR, 'local')

# Coastline for locations_clip_borders_to_land. OSM administrative boundaries
# run out to sea, so a coastal border claims open water: Gdansk is 262 km2 of
# land but its gmina relation measures 396. Load land-polygons-complete-4326
# from osmdata.openstreetmap.de into this table to trim them back.
LOCATIONS_LAND_POLYGONS_TABLE = os.environ.get(
    'LOCATIONS_LAND_POLYGONS_TABLE', 'locations_land'
)

# The node index goes to disk here rather than into RAM. The package default
# is memory, which is fine for a country; France is 4.8GB of extract and its
# index does not fit beside an 8GB PostGIS on this machine. Overriding this
# with flex_mem is how a France import grew the editor's cgroup by twelve
# gigabytes in three minutes and had systemd-oomd kill the session.
#
# The index file lives under LOCATIONS_CACHE_DIR and is removed afterwards.
LOCATIONS_LOCATION_STORAGE = (
    os.environ.get('LOCATIONS_LOCATION_STORAGE') or 'sparse_file_array'
)

ROOT_URLCONF = 'local.urls'
ROOT_URL = os.environ.get('LOCATIONS_ROOT_URL', 'localhost:8000')
USE_TZ = True

# GeoDjango needs the native libraries; set these when they are not on the
# loader path (for instance when they come from Python wheels).
if os.environ.get('GEOS_LIBRARY_PATH'):
    GEOS_LIBRARY_PATH = os.environ['GEOS_LIBRARY_PATH']
if os.environ.get('GDAL_LIBRARY_PATH'):
    GDAL_LIBRARY_PATH = os.environ['GDAL_LIBRARY_PATH']

REST_FRAMEWORK = {}

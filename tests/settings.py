"""Settings for the test suite."""
import os

SECRET_KEY = 'test-only-not-a-secret'
DEBUG = False
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'django.contrib.gis',
    'django.contrib.postgres',
    'django_osm_locations',
    'rest_framework',
    'django_filters',
]

DATABASES = {
    'default': {
        'ENGINE': 'django.contrib.gis.db.backends.postgis',
        'NAME': os.environ.get('LOCATIONS_TEST_DB_NAME', 'locations_test'),
        'USER': os.environ.get('LOCATIONS_TEST_DB_USER', 'locations'),
        'PASSWORD': os.environ.get('LOCATIONS_TEST_DB_PASSWORD', 'locations'),
        'HOST': os.environ.get('LOCATIONS_TEST_DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('LOCATIONS_TEST_DB_PORT', '55432'),
    }
}

# GeoDjango needs the native libs; point at them when they are not on the
# loader path (e.g. wheels that bundle their own).
if os.environ.get('GEOS_LIBRARY_PATH'):
    GEOS_LIBRARY_PATH = os.environ['GEOS_LIBRARY_PATH']
if os.environ.get('GDAL_LIBRARY_PATH'):
    GDAL_LIBRARY_PATH = os.environ['GDAL_LIBRARY_PATH']

ROOT_URLCONF = 'tests.urls'
ROOT_URL = 'example.com'
USE_TZ = True

MIDDLEWARE = [
    'django_osm_locations.middleware.PlaceSubdomainMiddleware',
]

REST_FRAMEWORK = {
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.OrderingFilter',
    ],
}

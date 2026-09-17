from django.apps import AppConfig


class LocationsConfig(AppConfig):
    name = 'django_osm_locations'

    # The app was historically installed as ``<project>.locations``, so every
    # existing migration, table and content type is namespaced under the
    # ``locations`` label. Keep it: renaming the label would orphan the tables
    # of every database created before the package was split out.
    label = 'locations'

    verbose_name = 'OSM Locations'

    # Existing tables use implicit AutoField primary keys. Declaring it here
    # avoids Django 3.2+ emitting a project-wide migration that would rewrite
    # every primary key column.
    default_auto_field = 'django.db.models.AutoField'

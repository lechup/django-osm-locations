"""Packaging invariants that are cheap to assert and expensive to get wrong."""
import django_osm_locations
from django.apps import apps


def test_app_label_is_preserved():
    """
    The app was historically installed as <project>.locations. Changing the
    label would orphan every existing table and migration record.
    """
    config = apps.get_app_config('locations')
    assert config.name == 'django_osm_locations'
    assert config.label == 'locations'


def test_table_names_are_unchanged():
    assert apps.get_model('locations', 'Place')._meta.db_table == 'locations_place'
    assert apps.get_model('locations', 'Street')._meta.db_table == 'locations_street'


def test_primary_key_type_is_not_silently_widened():
    """
    default_auto_field is pinned to AutoField; letting it default to
    BigAutoField would generate a migration rewriting every primary key.
    """
    pk = apps.get_model('locations', 'Place')._meta.pk
    assert pk.get_internal_type() == 'AutoField'


def test_version_is_exposed():
    assert django_osm_locations.__version__


def test_importer_modules_do_not_require_imposm_at_import_time():
    """
    imposm has no Python 3 release on PyPI, so it must not be imported at module
    level -- otherwise every management command disappears from manage.py help.
    """
    import importlib

    module = importlib.import_module(
        'django_osm_locations.management.commands.importers.base'
    )
    assert hasattr(module, '_load_osm_parser')

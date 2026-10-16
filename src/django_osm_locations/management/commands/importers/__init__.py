"""
Country importers, discovered by what they declare about themselves.

Adding a country means adding one module here. The class states which
Geofabrik extract it reads and which country it represents, and
``locations_import`` finds it from that -- there is no separate table of
download paths or slugs to keep in step.
"""
import importlib
import pkgutil

from .base import BaseImporter


def _importer_modules():
    for module in pkgutil.iter_modules(__path__):
        if module.name in ('base', 'nordic'):
            continue          # shared machinery, not a country
        yield importlib.import_module('{}.{}'.format(__name__, module.name))


def registry():
    """
    ``{name: importer class}`` for every country that declares itself.

    The name is the Geofabrik extract's, unless the class says otherwise:
    San Marino and the Vatican have no extract of their own and read Italy's,
    so they need a name of their own beside it.

    Built by walking this package, so a new module is picked up with no edit
    anywhere else.
    """
    found = {}
    for module in _importer_modules():
        for attribute in vars(module).values():
            if (isinstance(attribute, type)
                    and issubclass(attribute, BaseImporter)
                    and attribute.geofabrik_name
                    # the class must be defined here, not imported into it
                    and attribute.__module__ == module.__name__):
                found[attribute.name or attribute.geofabrik_name] = attribute
    return found


def get_importer(name):
    """The importer for a country's name, or None."""
    return registry().get(name)


def known_countries():
    return sorted(registry())

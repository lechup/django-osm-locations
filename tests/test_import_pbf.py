"""
End-to-end import of a real OSM extract.

Everything else in this suite tests pieces -- tag predicates, cache behaviour,
slug normalisation. This runs the actual pipeline: parse a .pbf, walk relations,
ways, coords and nodes, build geometry and write it to PostGIS. It is the only
test that would have caught, for instance, ``cascaded_union`` being removed in
Django 4.0, because that line is only reached with real multi-way boundaries.

Opt-in, because it needs a ~6MB download and takes about 20 seconds:

    pip install 'django-osm-locations[importers]'

    curl -L -o /tmp/isle-of-man.osm.pbf \
        https://download.geofabrik.de/europe/isle-of-man-latest.osm.pbf
    LOCATIONS_TEST_PBF=/tmp/isle-of-man.osm.pbf pytest -m pbf

The Isle of Man extract is used because it is the smallest Geofabrik country
extract that still contains the full hierarchy: admin_level 6 sheadings,
admin_level 8 parishes, and several thousand named streets.
"""
import io
import contextlib
import os

import pytest

pytestmark = [pytest.mark.pbf, pytest.mark.django_db]

EXTRACT = os.environ.get('LOCATIONS_TEST_PBF')

pytest.importorskip('osmium', reason='needs pyosmium')
pytest.importorskip('shapely')

if not EXTRACT or not os.path.exists(EXTRACT):
    pytest.skip(
        'set LOCATIONS_TEST_PBF to an Isle of Man extract (see module docstring)',
        allow_module_level=True,
    )


@pytest.fixture(scope='module')
def imported(django_db_setup, django_db_blocker):
    """Run the import once and let the assertions share it."""
    with django_db_blocker.unblock():
        from django.core.management import call_command

        from django_osm_locations.management.commands.importers.isle_of_man import (
            IsleOfManImporter,
        )

        call_command('loaddata', 'locations_country.json', verbosity=0)
        importer = IsleOfManImporter('isle-of-man', EXTRACT)
        with contextlib.redirect_stdout(io.StringIO()):
            importer.do_import()
        yield importer


def test_imports_the_sheadings(imported):
    """admin_level 6 -- there are exactly six, and they do not change."""
    from django_osm_locations.models import CountrySubarea

    names = set(CountrySubarea.objects.values_list('name', flat=True))
    assert {'Ayre', 'Garff', 'Michael', 'Rushen'} <= names
    assert CountrySubarea.objects.count() == 6


def test_imports_parishes_and_towns(imported):
    from django_osm_locations.models import Place

    names = set(Place.objects.values_list('name', flat=True))
    assert 'Douglas' in names
    assert {'Braddan', 'Marown', 'Arbory'} <= names
    assert Place.objects.count() > 50


def test_imports_streets(imported):
    from django_osm_locations.models import Street

    assert Street.objects.count() > 1000
    assert Street.objects.filter(name='Abbey Road').exists()


def test_geometry_is_populated(imported):
    from django_osm_locations.models import CountrySubarea, Street

    subarea = CountrySubarea.objects.exclude(border=None).first()
    assert subarea is not None
    assert subarea.border.num_geom >= 1
    assert subarea.area > 0
    assert subarea.center is not None
    assert len(subarea.bbox) == 4

    street = Street.objects.exclude(lines=None).first()
    assert street.length > 0
    assert street.center is not None


def test_borders_land_on_the_isle_of_man(imported):
    """Sanity-check the coordinates actually describe the island."""
    from django_osm_locations.models import CountrySubarea

    for subarea in CountrySubarea.objects.exclude(center=None):
        assert -5.0 < subarea.center[0] < -4.0, subarea.name
        assert 54.0 < subarea.center[1] < 54.5, subarea.name


def test_slugs_are_generated(imported):
    from django_osm_locations.models import Place

    for place in Place.objects.all()[:20]:
        assert place.slug
        assert place.slug == place.slug.lower()


def test_osm_ids_are_recorded(imported):
    from django_osm_locations.models import CountrySubarea, Street

    assert CountrySubarea.objects.exclude(relation_osm_id=None).exists()
    assert Street.objects.exclude(way_osm_id=None).exists()


def test_merge_marks_duplicate_streets(imported):
    """
    OSM splits one street into many ways, so the same name recurs; the merge
    pass is what collapses them.
    """
    from django_osm_locations.models import Street

    before = Street.objects.not_merged().count()
    with contextlib.redirect_stdout(io.StringIO()):
        imported.do_merge()
    after = Street.objects.not_merged().count()
    assert after < before, 'merge did not collapse any duplicate ways'


def test_reimport_updates_in_place(imported):
    """
    Writes are batched now, so the upsert semantics of update_or_create have to
    be preserved explicitly: a second import must update rows, not duplicate
    them.
    """
    from django_osm_locations.models import CountrySubarea, Place, Street
    from django_osm_locations.management.commands.importers.isle_of_man import (
        IsleOfManImporter,
    )

    before = {m.__name__: m.objects.count() for m in (CountrySubarea, Place, Street)}
    sample = Street.objects.order_by('way_osm_id').first()
    sample_id = sample.id

    importer = IsleOfManImporter('isle-of-man', EXTRACT)
    with contextlib.redirect_stdout(io.StringIO()):
        importer.do_import()

    after = {m.__name__: m.objects.count() for m in (CountrySubarea, Place, Street)}
    assert after == before, 'second import created duplicate rows'
    assert Street.objects.get(way_osm_id=sample.way_osm_id).id == sample_id

    stats = importer.writers[(Street, 'way_osm_id')]
    assert stats.created == 0
    assert stats.updated > 1000, 'the update path should have been exercised'

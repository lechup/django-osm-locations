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
        from django_osm_locations.management.commands.importers.isle_of_man import (
            IsleOfManImporter,
        )

        # the country rows come from migration 0031; loading the fixture here
        # would overwrite them and take any border with it
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


def test_imports_addresses(imported):
    """
    The address pass, which for a long time was executed here and asserted
    nowhere -- so a missing writer for (Address, 'node_osm_id') got through
    this tier and surfaced 52 minutes into a Poland import instead.
    """
    from django_osm_locations.models import Address

    assert Address.objects.exists()
    assert Address.objects.exclude(house_number='').count() == Address.objects.count()


def test_addresses_name_their_street(imported):
    """
    What makes an address worth having: it says where it is, which is the one
    thing a street geometry does not.
    """
    from django_osm_locations.models import Address

    with_street = Address.objects.exclude(street_name='').count()
    assert with_street > 0
    assert with_street / Address.objects.count() > 0.5


def test_addresses_are_inside_the_country(imported):
    """An extract is a rectangle; the Isle of Man is not."""
    from django_osm_locations.models import Address, Country

    border = Country.objects.get(slug='isle-of-man').border
    assert border is not None
    assert not Address.objects.exclude(center__intersects=border).exists()


def test_house_numbers_sort_the_way_a_street_is_walked(imported):
    """1, 2, 10 -- not 1, 10, 2, which is what a keyword sort gives."""
    from django_osm_locations.models import Address

    numbers = list(
        Address.objects.filter(house_number__regex=r'^\d+$')
        .order_by('house_number_sort', 'house_number')
        .values_list('house_number', flat=True)[:40]
    )
    assert numbers == sorted(numbers, key=int)


@pytest.fixture(scope='module')
def recalculated(imported, django_db_blocker):
    """The whole walk over the real extract, once, for the checks below."""
    with django_db_blocker.unblock():
        from django.core.management import call_command

        with contextlib.redirect_stdout(io.StringIO()):
            call_command('locations_recalculate_all', stdout=io.StringIO())
        yield


def _denorm_models():
    from django_osm_locations.models import (
        Address, CountrySubarea, Place, PlaceSubarea, Street,
    )
    return (CountrySubarea, Place, PlaceSubarea, Street, Address)


def test_no_placeholder_survives_the_walk(recalculated):
    """
    The walk used to write [['', '']] into every array and spend a whole pass
    stripping it again. It appends two-dimensional rows now and writes no
    placeholder at all -- so none may be left anywhere, on any model.
    """
    from django.db import connection

    for model in _denorm_models():
        table = model._meta.db_table
        for field in model.denorm_fields:
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT count(*) FROM "{}" WHERE {} @> ARRAY[ARRAY[\'\',\'\']]::varchar[]'
                    .format(table, field))
                assert cursor.fetchone()[0] == 0, '{}.{}'.format(table, field)


def test_every_filled_array_is_a_matrix_of_pairs(recalculated):
    """
    Appending a one-dimensional pair to an empty array gives a flat array, not
    a first row -- the reason the placeholder existed. Every append has to be
    two-dimensional for that to hold.
    """
    from django.db import connection

    for model in _denorm_models():
        table = model._meta.db_table
        for field in model.denorm_fields:
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT count(*) FROM "{0}" WHERE cardinality({1}) > 0 '
                    'AND (array_ndims({1}) <> 2 OR array_length({1}, 2) <> 2)'
                    .format(table, field))
                assert cursor.fetchone()[0] == 0, '{}.{}'.format(table, field)


def test_streets_and_addresses_know_their_country(recalculated):
    """Both inherit it from their place now; check they still get one."""
    from django_osm_locations.models import Address, Street

    streets = Street.objects.filter(is_merged=False)
    with_country = [s for s in streets if s.country and s.country[0][1]]
    assert len(with_country) / max(streets.count(), 1) > 0.9

    addresses = Address.objects.all()
    placed = [a for a in addresses if a.place and a.place[0][1]]
    assert placed, 'no address found its place'
    assert all(a.country and a.country[0][1] == 'isle-of-man' for a in placed)

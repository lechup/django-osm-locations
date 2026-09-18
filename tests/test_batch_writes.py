"""
BatchUpserter -- the buffered replacement for per-object update_or_create.

Writing a country's streets one update_or_create at a time was the single
slowest part of an import, costing a SELECT plus an INSERT/UPDATE round trip per
row. These pin the semantics that has to be preserved: an existing row is
updated in place rather than duplicated, and the _presave_* hooks still run even
though bulk_create/bulk_update bypass Model.save().
"""
import pytest
from django.contrib.gis.geos import LineString, MultiLineString, MultiPolygon

from django_osm_locations.management.commands.importers.base import BatchUpserter
from django_osm_locations.models import Place, Street

from .conftest import square

pytestmark = pytest.mark.django_db


def line(offset=0.0):
    return MultiLineString(LineString((offset, offset), (offset + 0.1, offset + 0.1)))


class TestDerivedFields:
    def test_derived_from_hook_names(self):
        writer = BatchUpserter(Street, 'way_osm_id')
        assert writer.derived_fields == {'bbox', 'center', 'length', 'slug'}

    def test_place_derives_area_and_population(self):
        writer = BatchUpserter(Place, 'relation_osm_id')
        assert writer.derived_fields == {'area', 'bbox', 'center', 'population', 'slug'}


class TestInsert:
    def test_creates_rows_on_flush(self):
        writer = BatchUpserter(Street, 'way_osm_id')
        writer.add(1, {'name': 'Abbey Road', 'lines': line()})
        assert Street.objects.count() == 0, 'nothing should be written before flush'

        writer.flush()
        assert Street.objects.count() == 1
        assert writer.created == 1

    def test_presave_hooks_run_on_create(self):
        """bulk_create bypasses save(), so the hooks must be invoked explicitly."""
        writer = BatchUpserter(Street, 'way_osm_id')
        writer.add(1, {'name': 'Kraków Street', 'lines': line()})
        writer.flush()

        street = Street.objects.get(way_osm_id=1)
        assert street.slug == 'krakow-street'
        assert street.length > 0
        assert street.center is not None
        assert len(street.bbox) == 4

    def test_flushes_automatically_at_batch_size(self):
        writer = BatchUpserter(Street, 'way_osm_id', batch_size=3)
        for osm_id in range(1, 4):
            writer.add(osm_id, {'name': 'S%d' % osm_id, 'lines': line(osm_id / 100)})
        assert Street.objects.count() == 3, 'should flush without an explicit call'

    def test_flush_is_idempotent_when_empty(self):
        writer = BatchUpserter(Street, 'way_osm_id')
        writer.flush()
        writer.flush()
        assert Street.objects.count() == 0


class TestUpdate:
    def test_updates_existing_row_instead_of_duplicating(self):
        Street.objects.create(way_osm_id=1, name='Old Name', lines=line())
        original = Street.objects.get(way_osm_id=1)

        writer = BatchUpserter(Street, 'way_osm_id')
        writer.add(1, {'name': 'New Name', 'lines': line(0.5)})
        writer.flush()

        assert Street.objects.count() == 1
        refreshed = Street.objects.get(way_osm_id=1)
        assert refreshed.id == original.id
        assert refreshed.name == 'New Name'
        assert writer.updated == 1
        assert writer.created == 0

    def test_mixed_batch_splits_creates_and_updates(self):
        Street.objects.create(way_osm_id=1, name='Existing', lines=line())

        writer = BatchUpserter(Street, 'way_osm_id')
        writer.add(1, {'name': 'Updated', 'lines': line()})
        writer.add(2, {'name': 'Brand New', 'lines': line(0.2)})
        writer.flush()

        assert writer.created == 1
        assert writer.updated == 1
        assert Street.objects.count() == 2
        assert Street.objects.get(way_osm_id=1).name == 'Updated'

    def test_last_write_wins_within_a_batch(self):
        """Matches repeated update_or_create on the same key."""
        writer = BatchUpserter(Street, 'way_osm_id')
        writer.add(1, {'name': 'First', 'lines': line()})
        writer.add(1, {'name': 'Second', 'lines': line()})
        writer.flush()

        assert Street.objects.count() == 1
        assert Street.objects.get(way_osm_id=1).name == 'Second'


class TestPlaces:
    def test_border_model_derives_area_and_bbox(self):
        writer = BatchUpserter(Place, 'relation_osm_id')
        writer.add(7, {'name': 'Douglas', 'border': MultiPolygon(square(1, 1))})
        writer.flush()

        place = Place.objects.get(relation_osm_id=7)
        assert place.slug == 'douglas'
        assert place.area > 0
        assert place.center is not None
        assert len(place.bbox) == 4

    def test_population_junk_is_coerced(self):
        writer = BatchUpserter(Place, 'relation_osm_id')
        writer.add(7, {'name': 'X', 'border': MultiPolygon(square(2, 2)), 'population': '~900'})
        writer.flush()
        assert Place.objects.get(relation_osm_id=7).population == 0

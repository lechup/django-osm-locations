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


class TestWritersAreNotDeclaredInAdvance:
    """
    Every model/lookup-field pair an import path writes needs a writer, and a
    fixed declaration of them is a trap: the address pass arrived with two
    lookup fields of its own, nothing added them, and the KeyError surfaced 52
    minutes into a real import with every other pass already done.
    """

    @staticmethod
    def _importer():
        from django_osm_locations.management.commands.importers.poland import (
            PolandImporter,
        )

        return PolandImporter('polska', 'unused.osm.pbf')

    @pytest.mark.django_db
    @pytest.mark.parametrize('model_name,field', [
        ('Address', 'node_osm_id'),
        ('Address', 'way_osm_id'),
        ('Place', 'relation_osm_id'),
        ('Place', 'way_osm_id'),
        ('Place', 'node_osm_id'),
        ('Street', 'relation_osm_id'),
        ('Street', 'way_osm_id'),
        ('CountrySubarea', 'relation_osm_id'),
        ('PlaceSubarea', 'relation_osm_id'),
    ])
    def test_a_writer_appears_for_any_pair_a_pass_uses(self, model_name, field):
        from django.apps import apps

        model = apps.get_model('locations', model_name)
        importer = self._importer()
        importer._upsert(model, field, 1, {'name': 'somewhere'})
        assert (model, field) in importer.writers

    @pytest.mark.django_db
    def test_the_same_pair_reuses_one_writer(self):
        """Otherwise each row would be its own batch, which is the thing the
        buffering exists to avoid."""
        from django.apps import apps

        model = apps.get_model('locations', 'Address')
        importer = self._importer()
        importer._upsert(model, 'node_osm_id', 1, {'name': 'a'})
        first = importer.writers[(model, 'node_osm_id')]
        importer._upsert(model, 'node_osm_id', 2, {'name': 'b'})
        assert importer.writers[(model, 'node_osm_id')] is first

    @pytest.mark.django_db
    def test_flushing_covers_writers_made_on_the_way(self):
        from django.apps import apps

        model = apps.get_model('locations', 'Address')
        importer = self._importer()
        importer._upsert(model, 'node_osm_id', 12345, {
            'name': 'Łąkowa 5', 'house_number': '5', 'street_name': 'Łąkowa',
        })
        importer._flush_writes()
        assert model.objects.filter(node_osm_id=12345).exists()


class TestStatisticsKeepUp:
    """
    A country's database starts with tables never analysed, and the import is
    one transaction autovacuum cannot see into: every lookup of a batch's ids
    was planned as a scan of the whole table.
    """

    def analyses(self, writer, rows):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            for osm_id in range(1, rows + 1):
                writer.add(osm_id, {'name': 'Road {}'.format(osm_id), 'lines': line()})
            writer.flush()
        return [q['sql'] for q in queries if q['sql'].startswith('ANALYZE')]

    def test_the_table_is_analysed_once_it_has_grown(self):
        writer = BatchUpserter(Street, 'way_osm_id', batch_size=2)
        writer.ANALYZE_FROM = 4
        assert self.analyses(writer, 4) == ['ANALYZE "locations_street"']

    def test_then_each_time_it_doubles(self):
        writer = BatchUpserter(Street, 'way_osm_id', batch_size=2)
        writer.ANALYZE_FROM = 4
        # at 4, 8 and 16 rows; not at every batch in between
        assert len(self.analyses(writer, 16)) == 3

    def test_a_small_import_never_pays_for_it(self):
        writer = BatchUpserter(Street, 'way_osm_id', batch_size=2)
        assert self.analyses(writer, 6) == []

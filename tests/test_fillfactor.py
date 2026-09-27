"""
Room on the page for the ancestry rewrites.

The recalculation rewrites every street and address once per ancestor that
contains it. Those rewrites touch only the denormalised arrays, which no index
covers, so they can be HOT updates -- the new version on the same page, not one
of the seventeen indexes on locations_address touched. HOT needs free space, and
fillfactor 100 leaves none, so each rewrite moved pages and maintained every
index. The address table went from 5.6GB after import to 19GB.
"""
import pytest
from django.db import connection


@pytest.mark.django_db
class TestTheBigTablesLeaveRoom:
    @staticmethod
    def fillfactor(table):
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT option_value FROM pg_class,
                    pg_options_to_table(reloptions)
                WHERE relname = %s AND option_name = 'fillfactor'
            """, [table])
            row = cursor.fetchone()
        return int(row[0]) if row else None

    @pytest.mark.parametrize('table', ['locations_address', 'locations_street'])
    def test_it_is_set(self, table):
        assert self.fillfactor(table) == 70

    @pytest.mark.parametrize('table', ['locations_place', 'locations_country'])
    def test_the_small_tables_are_left_packed(self, table):
        """
        They are rewritten too, but they are small enough that the bloat does
        not matter and packing them keeps reads cheap.
        """
        assert self.fillfactor(table) is None

    @pytest.mark.parametrize('table,column', [
        ('locations_address', 'country'),
        ('locations_address', 'countrysubarea'),
        ('locations_address', 'place'),
        ('locations_address', 'street'),
        ('locations_street', 'country'),
        ('locations_street', 'place'),
    ])
    def test_no_index_covers_the_arrays_a_rewrite_touches(self, table, column):
        """
        This is the premise the whole thing rests on: an index over one of
        these would make every rewrite non-HOT again whatever the fillfactor,
        and the fix would quietly stop working.
        """
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT count(*) FROM pg_indexes
                WHERE tablename = %s AND indexdef ~ ('\\(' || %s || '\\)')
            """, [table, column])
            assert cursor.fetchone()[0] == 0

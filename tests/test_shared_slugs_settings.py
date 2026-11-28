"""
The shared-slug step plans its one large query within bounds.

Germany's 20 million addresses, grouped by slug with parallel workers and a
server's work_mem, took a 14 GB machine past 84% memory pressure twice: the
import guard stopped one run and systemd-oomd killed another.
"""
import pytest
from django.db import connection, transaction

from django_osm_locations.management.commands.locations_recalculate_denormalized_fields import Command as Denorm

pytestmark = pytest.mark.django_db


def test_no_workers_and_a_small_work_mem():
    assert Denorm.SHARED_SLUGS_SETTINGS == (
        'SET LOCAL max_parallel_workers_per_gather = 0',
        "SET LOCAL work_mem = '64MB'",
    )


def test_the_query_is_planned_with_them():
    def show(name):
        with connection.cursor() as cursor:
            cursor.execute('SHOW ' + name)
            return cursor.fetchone()[0]

    seen = []
    original = Denorm.get_sql_for_shared_address_slugs
    try:
        def spying():
            seen.append((show('work_mem'), show('max_parallel_workers_per_gather')))
            return original()
        Denorm.get_sql_for_shared_address_slugs = staticmethod(spying)
        with transaction.atomic():
            Denorm.qualify_shared_address_slugs()
    finally:
        Denorm.get_sql_for_shared_address_slugs = original
    assert seen == [('64MB', '0')]

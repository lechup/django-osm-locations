"""
Django's query log, and why a bulk import cannot afford it.

With DEBUG on, Django keeps the last 9000 statements it executed, fully
interpolated. One bulk_create of a thousand streets leaves 0.6MB of SQL
behind -- measured -- so a full deque is 4.9GB. A Poland import grew from
0.02GB to 3.58GB of anonymous memory across its write phases and was stopped
by the memory-pressure watchdog 45 minutes in.
"""
from collections import deque

import pytest
from django.db import connection
from django.test import override_settings

from django_osm_locations.utils.query_log import stop_logging_queries


class Recorder:
    def __init__(self):
        self.lines = []

    def write(self, line):
        self.lines.append(line)


@pytest.fixture
def restore_log():
    """The log is per connection and shared, so put it back afterwards."""
    original = connection.queries_log
    yield
    connection.queries_log = original


class TestTurningItOff:
    @override_settings(DEBUG=True)
    def test_statements_stop_accumulating(self, restore_log, db):
        stop_logging_queries()
        for _ in range(5):
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
        assert len(connection.queries_log) == 0

    @override_settings(DEBUG=True)
    def test_it_says_so_once(self, restore_log, db):
        out = Recorder()
        stop_logging_queries(out)
        assert len(out.lines) == 1
        assert 'DEBUG' in out.lines[0]

    @override_settings(DEBUG=False)
    def test_nothing_happens_when_debug_is_off(self, restore_log, db):
        """There is no log to bound, and no reason to say anything."""
        out = Recorder()
        assert stop_logging_queries(out) == []
        assert out.lines == []

    @override_settings(DEBUG=True)
    def test_it_does_not_clear_partway_through(self, restore_log, db):
        """
        Clearing would move the ground under CaptureQueriesContext and
        assertNumQueries; a test that counts an importer's queries has every
        right to keep working, so the log is replaced rather than emptied.
        """
        connection.queries_log = deque(maxlen=9000)
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        before = len(connection.queries_log)
        assert before == 1

        stop_logging_queries()
        assert connection.queries_log is not None
        assert connection.queries_log.maxlen == 0


class TestTheCommandsDefendThemselves:
    @pytest.mark.parametrize('module', [
        'locations_import',
        'locations_recalculate_slugs',
        'locations_recalculate_denormalized_fields',
    ])
    def test_the_long_commands_call_it(self, module):
        """
        These are the ones that write in batches for tens of minutes. Anything
        added beside them should call it too.
        """
        import importlib
        import inspect

        command = importlib.import_module(
            'django_osm_locations.management.commands.%s' % module)
        source = inspect.getsource(command.Command.handle)
        assert 'stop_logging_queries' in source

"""
Durability, and what a recalculation is allowed to give up.

Every commit normally waits for the write-ahead log to reach disk. That wait is
worth having for a web request and worth nothing for a pass that writes tens of
millions of row versions and whose remedy for being interrupted is to run it
again from the start.
"""
import pytest
from django.db import connection

from django_osm_locations.utils.bulk_writes import relax_durability


class Recorder:
    def __init__(self):
        self.lines = []

    def write(self, line):
        self.lines.append(line)


@pytest.mark.django_db(transaction=True)
class TestRelaxingIt:
    @staticmethod
    def current():
        with connection.cursor() as cursor:
            cursor.execute('SHOW synchronous_commit')
            return cursor.fetchone()[0]

    def test_it_is_off_afterwards(self):
        assert relax_durability() is True
        assert self.current() == 'off'

    def test_it_says_what_it_gave_up(self):
        out = Recorder()
        relax_durability(out)
        assert len(out.lines) == 1
        assert 'synchronous_commit' in out.lines[0]
        assert 're-run' in out.lines[0]

    def test_it_is_this_session_only(self):
        """
        SET, not ALTER SYSTEM: nothing else connecting to the database has its
        durability changed by a recalculation running.
        """
        import inspect

        from django_osm_locations.utils import bulk_writes

        source = inspect.getsource(bulk_writes.relax_durability)
        assert 'SET synchronous_commit' in source
        assert 'ALTER SYSTEM' not in source


class TestTheLongCommandsAskForIt:
    @pytest.mark.parametrize('module', [
        'locations_recalculate_slugs',
        'locations_recalculate_denormalized_fields',
        'locations_recalculate_areas_and_lengths',
    ])
    def test_they_call_it(self, module):
        import importlib
        import inspect

        command = importlib.import_module(
            'django_osm_locations.management.commands.%s' % module)
        assert 'relax_durability' in inspect.getsource(command.Command.handle)


class TestItNeverFailsTheCommand:
    """
    It is a performance hint. Breaking a recalculation because the hint could
    not be applied would be the wrong trade, and it did break twelve tests that
    deliberately run without database access.
    """

    def test_no_database_access_is_not_an_error(self, monkeypatch):
        from django_osm_locations.utils import bulk_writes

        def refuse():
            raise RuntimeError('Database access not allowed')

        monkeypatch.setattr(bulk_writes.connection, 'cursor', refuse)
        assert bulk_writes.relax_durability() is False

    def test_it_says_nothing_when_it_could_not_apply(self, monkeypatch):
        from django_osm_locations.utils import bulk_writes

        def refuse():
            raise RuntimeError('nope')

        monkeypatch.setattr(bulk_writes.connection, 'cursor', refuse)
        out = Recorder()
        bulk_writes.relax_durability(out)
        assert out.lines == []

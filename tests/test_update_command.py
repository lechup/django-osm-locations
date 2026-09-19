"""
locations_update: fetch OSM diffs, reimport, reindex.

The replication server is mocked here. The network path was exercised by hand
against Geofabrik (an extract rewound to sequence 4890 caught up to 4908), but
the command's own logic -- what it refuses to do, and what it chains -- is worth
pinning without a network dependency.
"""
import io
from unittest import mock

import pytest
from django.core.management import CommandError, call_command

UPDATE = 'django_osm_locations.management.commands.locations_update'


class FakeState:
    def __init__(self, sequence):
        self.sequence = sequence


class FakeHeader:
    def __init__(self, url='http://example.invalid/updates', sequence=100, timestamp='t'):
        self.url = url
        self.sequence = sequence
        self.timestamp = timestamp


@pytest.fixture
def extract(tmp_path, settings):
    """A file standing in for a downloaded extract."""
    settings.LOCATIONS_DATA_DIR = str(tmp_path)
    path = tmp_path / 'isle-of-man-latest.osm.pbf'
    path.write_bytes(b'not really a pbf')
    return path


@pytest.fixture
def replication():
    """Patch out the replication server and header reader."""
    server = mock.MagicMock()
    server.get_state_info.return_value = FakeState(118)

    def apply_diffs_to_file(infile, outfile, start_id, **kwargs):
        # the real call writes the merged extract, and the command then moves
        # it into place, so the fake has to produce a file too
        with open(outfile, 'wb') as handle:
            handle.write(b'updated extract')
        return (118, 0)

    server.apply_diffs_to_file.side_effect = apply_diffs_to_file

    utils = mock.MagicMock()
    utils.get_replication_header.return_value = FakeHeader()

    with mock.patch('{}._load_replication'.format(UPDATE),
                    return_value=(utils, lambda url: server)):
        yield server, utils


def run(*args, **kwargs):
    out = io.StringIO()
    call_command('locations_update', *args, stdout=out, **kwargs)
    return out.getvalue()


class TestRefusals:
    def test_unknown_country(self, replication):
        with pytest.raises(CommandError) as exc:
            run('atlantis')
        assert 'not a known country' in str(exc.value)

    def test_missing_extract(self, tmp_path, settings, replication):
        settings.LOCATIONS_DATA_DIR = str(tmp_path)
        with pytest.raises(CommandError) as exc:
            run('isle-of-man')
        assert 'locations_import' in str(exc.value), 'should say how to get an extract'

    def test_extract_without_replication_header(self, extract, replication):
        _, utils = replication
        utils.get_replication_header.return_value = FakeHeader(url=None, sequence=None)
        with pytest.raises(CommandError) as exc:
            run('isle-of-man')
        assert 'no replication header' in str(exc.value)


class TestDryRun:
    def test_reports_how_far_behind(self, extract, replication):
        output = run('isle-of-man', '--dry-run')
        assert 'at sequence 100' in output
        assert '18 behind' in output
        assert 'would download' in output

    def test_downloads_nothing(self, extract, replication):
        server, _ = replication
        run('isle-of-man', '--dry-run')
        server.apply_diffs_to_file.assert_not_called()

    def test_does_not_reindex(self, extract, replication):
        with mock.patch('{}.call_command'.format(UPDATE)) as chained:
            run('isle-of-man', '--dry-run')
        chained.assert_not_called()


class TestUpToDate:
    def test_stops_when_already_current(self, extract, replication):
        server, _ = replication
        server.get_state_info.return_value = FakeState(100)   # same as the file
        output = run('isle-of-man')
        assert 'already up to date' in output
        server.apply_diffs_to_file.assert_not_called()

    def test_nothing_is_chained_when_nothing_changed(self, extract, replication):
        server, _ = replication
        server.get_state_info.return_value = FakeState(100)
        with mock.patch('{}.call_command'.format(UPDATE)) as chained:
            run('isle-of-man')
        chained.assert_not_called()


class TestUpdate:
    @pytest.fixture(autouse=True)
    def no_import(self):
        """The importer itself is covered by the .pbf integration tests."""
        with mock.patch('{}.Command._reimport'.format(UPDATE)) as reimport:
            yield reimport

    @pytest.fixture(autouse=True)
    def chained(self):
        """Recalculate and reindex are covered by their own tests."""
        with mock.patch('{}.call_command'.format(UPDATE)) as call:
            yield call

    @staticmethod
    def names(chained):
        return [call.args[0] for call in chained.call_args_list]

    def test_downloads_from_the_next_sequence(self, extract, replication):
        server, _ = replication
        run('isle-of-man')
        assert server.apply_diffs_to_file.call_args[0][2] == 101, \
            'should resume at the sequence after the one in the file'

    def test_writes_to_a_temporary_file_with_a_known_extension(self, extract, replication):
        """libosmium picks the output format from the extension."""
        server, _ = replication
        run('isle-of-man')
        outfile = server.apply_diffs_to_file.call_args[0][1]
        assert outfile.endswith('.osm.pbf')
        assert outfile != str(extract), 'must not write over the live extract'

    def test_the_updated_extract_replaces_the_old_one(self, extract, replication):
        run('isle-of-man')
        assert extract.read_bytes() == b'updated extract'
        assert not list(extract.parent.glob('*.updating*')), 'temp file left behind'

    def test_reimports_after_updating(self, extract, replication, no_import):
        run('isle-of-man')
        no_import.assert_called_once()

    def test_chains_recalculate_then_reindex(self, extract, replication, chained):
        run('isle-of-man')
        assert self.names(chained) == ['locations_recalculate_all', 'locations_reindex']

    def test_no_reindex_flag(self, extract, replication, chained):
        run('isle-of-man', '--no-reindex')
        assert self.names(chained) == ['locations_recalculate_all']

    def test_no_recalculate_flag(self, extract, replication, chained):
        run('isle-of-man', '--no-recalculate')
        assert self.names(chained) == ['locations_reindex']


class TestImportRefusals:
    """
    locations_import used to print a line and exit 0 for an unknown country, so
    a typo reported success having done nothing -- easy to miss in a Makefile or
    a CI step, and easier still now that countries are positional arguments.
    """

    def test_unknown_country_is_an_error(self, db):
        out = io.StringIO()
        with pytest.raises(CommandError) as exc:
            call_command('locations_import', 'norwya', stdout=out)
        assert 'norwya' in str(exc.value)

    def test_the_error_lists_what_is_available(self, db):
        with pytest.raises(CommandError) as exc:
            call_command('locations_import', 'atlantis')
        message = str(exc.value)
        for known in ('poland', 'denmark', 'norway', 'finland', 'netherlands'):
            assert known in message

    def test_one_bad_name_stops_the_whole_run(self, db):
        """Rather than importing the good ones and quietly skipping the rest."""
        with pytest.raises(CommandError):
            call_command('locations_import', 'poland', 'atlantis')

    def test_a_missing_country_row_is_reported_before_downloading(self, db):
        """
        An extract can be gigabytes; finding out afterwards that the Country row
        is absent, via a bare DoesNotExist, is an expensive way to be told.
        """
        from django_osm_locations.models import Country

        Country.objects.filter(slug='polska').delete()
        with pytest.raises(CommandError) as exc:
            call_command('locations_import', 'poland')
        assert 'migrate' in str(exc.value)

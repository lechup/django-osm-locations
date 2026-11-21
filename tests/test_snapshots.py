"""
Publishing an index, and loading a published one.

Thirteen hours of importing, clipping, recalculating and indexing stand
between a fresh checkout and a working search. A published snapshot removes
all of it -- provided the two things the snapshot API does not do for us are
done, because either one leaves a consumer with a working index and an empty
API: a restore brings back index names but not aliases, and a second country
must join the first rather than evict it.
"""
import pytest

from django_osm_locations.search import snapshots
from django_osm_locations.search.indexes import INDEXES


class FakeIndices:
    def __init__(self, aliases=None, existing=()):
        self._aliases = aliases or {}
        self._existing = set(existing)
        self.alias_actions = []
        self.deleted = []
        #: shared with FakeCluster.log by a test that cares about the order
        self.log = None

    def exists_alias(self, name):
        return name in self._aliases

    def get_alias(self, name):
        return {index: {} for index in self._aliases.get(name, [])}

    def exists(self, index):
        return index in self._existing

    def update_aliases(self, body):
        self.alias_actions.extend(body['actions'])
        if self.log is not None:
            self.log.append('alias')

    def delete(self, index):
        self.deleted.append(index)
        if self.log is not None:
            self.log.append('delete')


class FakeSnapshot:
    def __init__(self, states=None):
        self.created = []
        self.restored = []
        self.repositories = []
        #: registrations and forgettings, in order
        self.ops = []
        self.fail_register = False
        #: states returned by successive get() calls, as a real snapshot
        #: reports IN_PROGRESS before it reports SUCCESS
        self.states = list(states or ['SUCCESS'])
        self.gets = 0

    def get(self, repository, snapshot):
        self.gets += 1
        state = self.states[min(self.gets - 1, len(self.states) - 1)]
        return {'snapshots': [{'snapshot': snapshot, 'state': state,
                               'indices': ['i'], 'shards': {'successful': 1, 'total': 1}}]}

    def create_repository(self, repository, body):
        if self.fail_register:
            raise RuntimeError('access denied')
        self.repositories.append((repository, body))
        self.ops.append(('register', repository))

    def delete_repository(self, repository):
        if not any(name == repository for name, _ in self.repositories):
            error = RuntimeError('repository_missing_exception')
            error.status_code = 404
            raise error
        self.repositories = [(n, b) for n, b in self.repositories if n != repository]
        self.ops.append(('forget', repository))

    def get_repository(self):
        return {name: {'settings': body['settings']}
                for name, body in self.repositories}

    def create(self, repository, snapshot, body, params):
        self.created.append((snapshot, body, params))

    def restore(self, repository, snapshot, body, params):
        self.restored.append((snapshot, body, params))
        self.retried_on_timeout = self.transport.retry_on_timeout


class FakeTransport:
    retry_on_timeout = True


def health(status, active=0, initializing=0, unassigned=0):
    return {'status': status, 'active_shards': active,
            'initializing_shards': initializing, 'unassigned_shards': unassigned}


class FakeCluster:
    """Successive health answers, as a restore goes from red to green."""

    def __init__(self, healths=None):
        self.healths = list(healths or [health('green', active=1)])
        self.polls = 0
        self.log = None
        self.explained = 0

    def health(self, index, params):
        self.polls += 1
        if self.log is not None:
            self.log.append('health')
        return self.healths[min(self.polls - 1, len(self.healths) - 1)]

    def allocation_explain(self):
        self.explained += 1
        return {'allocate_explanation': 'cannot allocate because allocation is not permitted',
                'node_allocation_decisions': [{'deciders': [
                    {'explanation': 'the node is above the high watermark'}]}]}


class FakeClient:
    def __init__(self, aliases=None, existing=(), healths=None):
        self.indices = FakeIndices(aliases, existing)
        self.indices.recovery = lambda index: {}
        self.transport = FakeTransport()
        self.snapshot = FakeSnapshot()
        self.snapshot.transport = self.transport
        self.cluster = FakeCluster(healths)


ALIASES = {
    'locations-streets': ['locations-streets-pl-20260101', 'locations-streets-dk-20260101'],
    'locations-places': ['locations-places-pl-20260101'],
}


class TestRepositoryRegistration:
    def test_a_cluster_without_path_repo_says_so(self):
        """
        OpenSearch reports this as access being denied, which reads like a
        filesystem permission problem and is not one.
        """
        client = FakeClient()
        client.snapshot.fail_register = True
        with pytest.raises(RuntimeError, match='path.repo'):
            snapshots.register_repository(client=client)

    def test_it_registers_a_filesystem_repository(self):
        client = FakeClient()
        snapshots.register_repository(client=client, location='/snapshots')
        _, body = client.snapshot.repositories[0]
        assert body['type'] == 'fs'
        assert body['settings']['location'] == '/snapshots'


class TestChoosingWhatToPackage:
    def test_one_country_only(self):
        found = snapshots.indexes_for(
            'pl', prefix='locations', client=FakeClient(ALIASES))
        assert found == ['locations-places-pl-20260101', 'locations-streets-pl-20260101']

    def test_everything_when_no_country_is_named(self):
        found = snapshots.indexes_for(
            None, prefix='locations', client=FakeClient(ALIASES))
        assert 'locations-streets-dk-20260101' in found
        assert len(found) == 3

    def test_it_packages_what_is_served_not_what_is_named(self):
        """
        Read from the aliases rather than recomputing names: a snapshot should
        hold what is being served, not what a convention says ought to exist.
        """
        client = FakeClient({'locations-streets': ['locations-streets-pl-19990101']})
        assert snapshots.indexes_for('pl', prefix='locations', client=client) == [
            'locations-streets-pl-19990101']

    def test_a_type_with_no_alias_is_skipped_rather_than_failing(self):
        client = FakeClient({'locations-streets': ['locations-streets-pl-20260101']})
        assert snapshots.indexes_for('pl', prefix='locations', client=client) == [
            'locations-streets-pl-20260101']

    def test_a_country_token_that_merely_starts_the_same_is_not_a_match(self):
        client = FakeClient({'locations-streets': ['locations-streets-plus-20260101']})
        assert snapshots.indexes_for('pl', prefix='locations', client=client) == []


class TestTheSnapshotItself:
    def test_it_polls_rather_than_holding_the_request_open(self):
        """
        `wait_for_completion` keeps one HTTP request open for the whole
        snapshot. At this size that outlives the client timeout, the client
        retries, and the retry is refused because a snapshot of that name is
        already running -- an error that reads as a naming conflict and is
        not one. Seen on the real cluster with a 4.3GB snapshot.
        """
        client = FakeClient()
        snapshots.create('pl-2026-09-24', ['locations-streets-pl-1'],
                         client=client, interval=0)
        _, _, params = client.snapshot.created[0]
        assert params['wait_for_completion'] == 'false'

    def test_it_still_waits_for_the_result(self):
        """Returning early would publish a snapshot that is not written yet."""
        client = FakeClient()
        client.snapshot.states = ['IN_PROGRESS', 'IN_PROGRESS', 'SUCCESS']
        state = snapshots.create('x', ['i'], client=client, interval=0)
        assert state == 'SUCCESS'
        assert client.snapshot.gets == 3

    def test_a_partial_snapshot_is_reported_not_swallowed(self):
        """
        OpenSearch does not treat PARTIAL as an error, so a snapshot missing
        shards would be published as though it were whole.
        """
        client = FakeClient()
        client.snapshot.states = ['PARTIAL']
        assert snapshots.create('x', ['i'], client=client, interval=0) == 'PARTIAL'

    def test_cluster_state_is_left_out(self):
        """It carries settings of the cluster that wrote it, not of the reader."""
        client = FakeClient()
        snapshots.create('x', ['i'], client=client)
        _, body, _ = client.snapshot.created[0]
        assert body['include_global_state'] is False


class TestRestoring:
    def test_aliases_are_not_taken_from_the_snapshot(self):
        """
        What the alias should cover depends on what else is already loaded,
        which the snapshot cannot know.
        """
        client = FakeClient()
        snapshots.restore('pl-2026-09-24', client=client)
        _, body, _ = client.snapshot.restored[0]
        assert body['include_aliases'] is False

    def test_it_does_not_hold_the_request_open_either(self):
        """A restore of this size outlives the timeout the same way."""
        client = FakeClient()
        snapshots.restore('x', client=client)
        _, _, params = client.snapshot.restored[0]
        assert params['wait_for_completion'] == 'false'

    def test_a_busy_cluster_is_given_time_to_take_it_on(self):
        """France's restore took longer than 30 s to be accepted, behind a disk writing out the pack."""
        client = FakeClient()
        snapshots.restore('x', client=client)
        _, _, params = client.snapshot.restored[0]
        assert params['request_timeout'] == snapshots.RESTORE_REQUEST_TIMEOUT >= 300

    def test_it_is_asked_once(self):
        client = FakeClient()
        snapshots.restore('x', client=client)
        assert client.snapshot.retried_on_timeout is False
        assert client.transport.retry_on_timeout is True

    def test_a_timeout_is_not_followed_by_a_refused_retry(self):
        """
        The retry of a restore already begun is refused as a clash -- "an open
        index with same name already exists" -- and that is what stopped FR.
        """
        opensearchpy = pytest.importorskip('opensearchpy')
        calls = []

        class TimingOut(opensearchpy.Urllib3HttpConnection):
            def perform_request(self, method, url, *args, **kwargs):
                calls.append((method, url, kwargs.get('timeout')))
                raise opensearchpy.ConnectionTimeout('TIMEOUT', 'timed out', None)

        client = opensearchpy.OpenSearch(
            hosts=['localhost:1'], connection_class=TimingOut,
            retry_on_timeout=True, max_retries=3)
        with pytest.raises(opensearchpy.ConnectionTimeout):
            snapshots.restore('fr-2026-10-31', client=client, repository='locations-fr')
        assert calls == [('POST', '/_snapshot/locations-fr/fr-2026-10-31/_restore',
                          snapshots.RESTORE_REQUEST_TIMEOUT)]
        assert client.transport.retry_on_timeout is True


class TestWaitingForTheRestore:
    """
    The restore call returns once the cluster accepts it; the shards come back
    afterwards, Germany's in about half an hour. Under an alias before then,
    an index fails every search through it -- the API answered 503 for that
    half hour.
    """

    def test_it_waits_until_every_primary_is_active(self):
        client = FakeClient(healths=[
            health('red', active=0, initializing=2, unassigned=4),
            health('red', active=4, initializing=2),
            health('green', active=6),
        ])
        assert snapshots.wait_for_restore(['i'], client=client, interval=0) == 'green'
        assert client.cluster.polls == 3

    def test_yellow_is_enough(self):
        """Every primary active is searchable; a single node never allocates replicas."""
        client = FakeClient(healths=[health('yellow', active=6, unassigned=6)])
        assert snapshots.wait_for_restore(['i'], client=client, interval=0) == 'yellow'

    def test_shards_waiting_their_turn_are_not_a_stall(self):
        """Behind the limit on concurrent recoveries, the rest wait unassigned."""
        waiting = [health('red', active=n, initializing=2, unassigned=40 - n) for n in range(30)]
        client = FakeClient(healths=waiting + [health('green', active=42)])
        assert snapshots.wait_for_restore(['i'], client=client, interval=0, stall_polls=3) == 'green'

    def test_a_stall_stops_it_with_the_clusters_reason(self):
        """A disk past its watermark refuses the shards; waiting does not bring them."""
        client = FakeClient(healths=[health('red', active=2, unassigned=4)])
        with pytest.raises(RuntimeError, match='high watermark'):
            snapshots.wait_for_restore(['i'], client=client, interval=0, stall_polls=3)
        assert client.cluster.polls == 3

    def test_an_index_never_created_is_a_stall_too(self):
        """The cluster answers red with no shard at all, and would forever."""
        client = FakeClient(healths=[health('red')])
        with pytest.raises(RuntimeError, match='stalled with 0 shard'):
            snapshots.wait_for_restore(['i'], client=client, interval=0, stall_polls=3)

    def test_progress_is_reported_with_the_bytes_copied(self):
        client = FakeClient(healths=[health('red', active=1, initializing=1), health('green', active=2)])
        client.indices.recovery = lambda index: {'i': {'shards': [
            {'index': {'size': {'recovered_in_bytes': 30, 'total_in_bytes': 100}}},
            {'index': {'size': {'recovered_in_bytes': 100, 'total_in_bytes': 100}}}]}}
        seen = []
        snapshots.wait_for_restore(['i'], client=client, interval=0,
                                   on_progress=lambda *a: seen.append(a))
        assert seen == [(1, 2, 130, 200), (2, 2, 130, 200)]


class TestPointingTheAliases:
    def test_a_restored_index_is_given_its_alias(self):
        """Without this the index is there and every query returns nothing."""
        client = FakeClient()
        snapshots.point_aliases(['locations-streets-pl-20260924'],
                                client=client, prefix='locations')
        assert client.indices.alias_actions == [
            {'add': {'index': 'locations-streets-pl-20260924',
                     'alias': 'locations-streets'}}
        ]

    def test_nothing_is_removed(self):
        """
        Adding rather than replacing is what lets Denmark join Poland instead
        of hiding it.
        """
        client = FakeClient(ALIASES)
        snapshots.point_aliases(['locations-streets-dk-20260924'],
                                client=client, prefix='locations')
        assert all('remove' not in action for action in client.indices.alias_actions)

    def test_each_type_finds_its_own_alias(self):
        client = FakeClient()
        snapshots.point_aliases(
            ['locations-streets-pl-1', 'locations-place-subareas-pl-1'],
            client=client, prefix='locations')
        assert {a['add']['alias'] for a in client.indices.alias_actions} == {
            'locations-streets', 'locations-place-subareas'}

    def test_the_longest_matching_type_wins(self):
        """
        `place-subareas` and `places` both begin the same way; matching the
        shorter one first would file a subarea under places.
        """
        assert snapshots._alias_for(
            'locations-place-subareas-pl-1', 'locations') == 'locations-place-subareas'

    def test_something_unrecognised_is_left_alone(self):
        client = FakeClient()
        snapshots.point_aliases(['some-other-index'], client=client, prefix='locations')
        assert client.indices.alias_actions == []


class TestEveryTypeCanBeRoundTripped:
    @pytest.mark.parametrize('name', sorted(INDEXES))
    def test_an_index_of_each_type_finds_its_alias(self, name):
        index = '{}-{}-pl-20260924'.format('locations', name)
        assert snapshots._alias_for(index, 'locations') == INDEXES[name].alias('locations')


class TestOneCountryPerRepository:
    """
    A filesystem repository keeps one manifest at its root naming every
    snapshot in it, so two countries in one repository are not two downloads:
    no subset of that directory restores either one on its own.
    """

    def test_each_country_gets_its_own_directory(self):
        assert snapshots.repository_for('pl') == ('locations-pl', '/snapshots/pl')
        assert snapshots.repository_for('dk') == ('locations-dk', '/snapshots/dk')

    def test_countries_do_not_share_a_directory(self):
        _, polish = snapshots.repository_for('pl')
        _, danish = snapshots.repository_for('dk')
        assert polish != danish

    def test_no_country_is_still_a_directory_of_its_own(self):
        """A single-country deployment snapshots everything; it still packs."""
        assert snapshots.repository_for(None) == ('locations-all', '/snapshots/all')

    def test_the_root_is_where_path_repo_points(self):
        name, location = snapshots.repository_for('pl', root='/var/snapshots/')
        assert location == '/var/snapshots/pl'
        assert name == 'locations-pl'


class TestTheLoadCommand:
    """
    Listing what a repository holds has to work before you know a name, and
    asking for nothing in particular has to say so rather than restore
    something arbitrary.
    """

    @staticmethod
    def _patch(monkeypatch, client):
        from django_osm_locations.management.commands import locations_import_snapshot as command

        monkeypatch.setattr(command, 'get_client', lambda: client)
        monkeypatch.setattr(command, 'get_index_prefix', lambda: 'locations')
        return command

    def test_listing_needs_no_snapshot_name(self, monkeypatch):
        from django.core.management import call_command

        client = FakeClient()
        self._patch(monkeypatch, client)
        call_command('locations_import_snapshot', list_only=True)
        assert client.snapshot.gets == 0  # nothing registered, nothing to ask

    def test_listing_a_named_directory_registers_it_first(self, monkeypatch):
        """
        A consumer who has just unpacked a download has a directory, not a
        repository, and no API can find it for them.
        """
        from django.core.management import call_command

        client = FakeClient()
        self._patch(monkeypatch, client)
        call_command('locations_import_snapshot', '--list', '--in', 'pl')
        assert client.snapshot.repositories[0][0] == 'locations-pl'
        assert client.snapshot.repositories[0][1]['settings']['location'] == '/snapshots/pl'

    def test_restoring_without_a_name_says_which(self, monkeypatch):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        self._patch(monkeypatch, FakeClient())
        with pytest.raises(CommandError, match='Which snapshot'):
            call_command('locations_import_snapshot')

    def test_the_directory_comes_from_the_snapshot_name(self, monkeypatch):
        """`pl-2026-09-24` was published from `pl`, and says so."""
        from django.core.management import call_command

        client = FakeClient()
        self._patch(monkeypatch, client)
        call_command('locations_import_snapshot', 'pl-2026-09-24')
        assert client.snapshot.repositories[0][0] == 'locations-pl'
        assert client.snapshot.restored[0][0] == 'pl-2026-09-24'

    def test_an_unpacked_elsewhere_snapshot_can_say_where(self, monkeypatch):
        from django.core.management import call_command

        client = FakeClient()
        self._patch(monkeypatch, client)
        call_command('locations_import_snapshot', 'pl-2026-09-24', '--in', 'poland-old')
        assert client.snapshot.repositories[0][1]['settings']['location'] == (
            '/snapshots/poland-old')


class TestAPackUnpackedAgain:
    """
    Every state packed here loaded as "nothing to load": deleting the snapshot
    after packing left the cluster remembering an emptied repository, and the
    pack unpacked into the same directory was read through that memory.
    """

    @staticmethod
    def _patch(monkeypatch, client):
        from django_osm_locations.management.commands import locations_import_snapshot as command

        monkeypatch.setattr(command, 'get_client', lambda: client)
        monkeypatch.setattr(command, 'get_index_prefix', lambda: 'locations')

    def test_the_cluster_forgets_the_directory_before_reading_it(self, monkeypatch):
        from django.core.management import call_command

        client = FakeClient()
        client.snapshot.create_repository('locations-pl', {'settings': {'location': '/snapshots/pl'}})
        self._patch(monkeypatch, client)
        call_command('locations_import_snapshot', 'pl-2026-09-24')
        assert client.snapshot.ops[-2:] == [('forget', 'locations-pl'), ('register', 'locations-pl')]

    def test_a_repository_never_registered_is_no_error(self, monkeypatch):
        from django.core.management import call_command

        client = FakeClient()
        self._patch(monkeypatch, client)
        call_command('locations_import_snapshot', 'pl-2026-09-24')
        assert client.snapshot.restored[0][0] == 'pl-2026-09-24'

    def test_a_directory_with_snapshot_files_and_none_listed_says_why(self, monkeypatch, tmp_path):
        """A newer, emptied index-N left beside the pack's is read first."""
        from io import StringIO

        from django.core.management import call_command

        (tmp_path / 'be').mkdir()
        (tmp_path / 'be' / 'snap-x.dat').write_bytes(b'')
        client = FakeClient()
        client.snapshot.get = lambda repository, snapshot: {'snapshots': []}
        self._patch(monkeypatch, client)
        out = StringIO()
        call_command('locations_import_snapshot', 'be', '--host-root', str(tmp_path), stdout=out)
        assert 'nothing to load' in out.getvalue()
        assert 'unpack the pack again' in out.getvalue()


class TestThePackageCommand:
    @staticmethod
    def _patch(monkeypatch, client):
        from django_osm_locations.management.commands import locations_export_snapshot as command

        monkeypatch.setattr(command, 'get_client', lambda: client)
        monkeypatch.setattr(command, 'get_index_prefix', lambda: 'locations')
        # what was merged, and how many snapshots had been taken by then
        client.merged = []
        monkeypatch.setattr(command, 'merge', lambda indexes, client: client.merged.append(
            (indexes, len(client.snapshot.created))))
        return command

    def test_a_country_is_written_to_its_own_directory(self, monkeypatch):
        from django.core.management import call_command

        client = FakeClient(ALIASES)
        self._patch(monkeypatch, client)
        call_command('locations_export_snapshot', 'pl', '--name', 'pl-2026-09-24')

        assert client.snapshot.repositories[0][0] == 'locations-pl'
        assert client.snapshot.repositories[0][1]['settings']['location'] == '/snapshots/pl'
        name, body, _ = client.snapshot.created[0]
        assert name == 'pl-2026-09-24'
        assert body['indices'] == (
            'locations-places-pl-20260101,locations-streets-pl-20260101')

    def test_what_it_packages_is_merged_before_the_snapshot(self, monkeypatch):
        from django.core.management import call_command

        client = FakeClient(ALIASES)
        self._patch(monkeypatch, client)
        call_command('locations_export_snapshot', 'pl', '--name', 'pl-2026-09-24')
        assert client.merged == [
            (['locations-places-pl-20260101', 'locations-streets-pl-20260101'], 0)]

    def test_a_merge_that_fails_is_not_published(self, monkeypatch):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        client = FakeClient(ALIASES)
        command = self._patch(monkeypatch, client)

        def fails(indexes, client):
            raise RuntimeError('4 deleted documents are left after the merge')
        monkeypatch.setattr(command, 'merge', fails)
        with pytest.raises(CommandError, match='4 deleted documents'):
            call_command('locations_export_snapshot', 'pl')
        assert client.snapshot.created == []

    def test_it_refuses_a_country_that_is_not_indexed(self, monkeypatch):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        self._patch(monkeypatch, FakeClient(ALIASES))
        with pytest.raises(CommandError, match='Nothing is aliased'):
            call_command('locations_export_snapshot', 'fr')

    def test_a_partial_snapshot_is_not_published_quietly(self, monkeypatch):
        """
        OpenSearch does not treat PARTIAL as an error, so a snapshot missing
        shards would go out looking whole.
        """
        from django.core.management import call_command
        from django.core.management.base import CommandError

        client = FakeClient(ALIASES)
        client.snapshot.states = ['PARTIAL']
        self._patch(monkeypatch, client)
        with pytest.raises(CommandError, match='PARTIAL'):
            call_command('locations_export_snapshot', 'pl')


class TestLoadingNeedsNoDatabase:
    """
    A consumer of the published packs runs OpenSearch and the API, and no
    PostgreSQL: the import side is the only one that needs it. Loading a pack
    must therefore never open a database connection.
    """

    def test_a_pack_loads_with_the_database_unreachable(self, monkeypatch):
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command
        from django.db.backends.base.base import BaseDatabaseWrapper

        def refuse(*args, **kwargs):
            raise AssertionError('loading a pack opened a database connection')

        monkeypatch.setattr(BaseDatabaseWrapper, 'ensure_connection', refuse)

        from django_osm_locations.search.client import get_index_prefix

        prefix = get_index_prefix()
        client = FakeClient()
        indexes = ['{}-streets-pl-20261002'.format(prefix), '{}-addresses-pl-20261002'.format(prefix)]
        client.snapshot.get = lambda repository, snapshot: {'snapshots': [{
            'snapshot': snapshot, 'state': 'SUCCESS', 'indices': indexes,
            'shards': {'successful': 2, 'total': 2}}]}

        out = StringIO()
        with mock.patch(
                'django_osm_locations.management.commands.locations_import_snapshot.get_client',
                return_value=client), \
                mock.patch('django_osm_locations.search.snapshots.get_client', return_value=client):
            call_command('locations_import_snapshot', 'pl-2026-10-02', stdout=out)

        assert client.snapshot.restored, 'nothing was restored'
        assert {a['add']['alias'] for a in client.indices.alias_actions} == {
            '{}-streets'.format(prefix), '{}-addresses'.format(prefix)}


class TestTheLoadWaitsForTheShards:
    """
    Loading a newer pack of a country already served must not leave a gap:
    the old version answers until the new one can, and a restore that never
    gets there changes nothing that answers.
    """

    NEW = ['locations-places-pl-20261120000000', 'locations-streets-pl-20261120000000']
    OLDER = 'locations-streets-pl-20261017000000'

    def _load(self, monkeypatch, client):
        from io import StringIO

        from django.core.management import call_command
        from django_osm_locations.management.commands import locations_import_snapshot as command

        monkeypatch.setattr(command, 'get_client', lambda: client)
        monkeypatch.setattr(command, 'get_index_prefix', lambda: 'locations')
        monkeypatch.setattr('time.sleep', lambda seconds: None)
        new = self.NEW
        client.snapshot.get = lambda repository, snapshot: {'snapshots': [{
            'snapshot': snapshot, 'state': 'SUCCESS', 'indices': new,
            'shards': {'successful': 2, 'total': 2}}]}
        out = StringIO()
        call_command('locations_import_snapshot', 'pl-2026-11-20', stdout=out)
        return out.getvalue()

    def test_nothing_is_aliased_or_retired_before_the_shards_are_back(self, monkeypatch):
        client = FakeClient(aliases={'locations-streets': [self.OLDER]}, healths=[
            health('red', initializing=2), health('red', active=1, initializing=1),
            health('green', active=2)])
        client.cluster.log = client.indices.log = log = []
        out = self._load(monkeypatch, client)
        assert log == ['health', 'health', 'health', 'alias', 'alias', 'delete']
        assert client.indices.deleted == [self.OLDER]
        assert 'of 2 shard(s) active' in out

    def test_a_restore_that_stalls_changes_nothing_that_answers(self, monkeypatch):
        from django.core.management.base import CommandError

        client = FakeClient(aliases={'locations-streets': [self.OLDER]},
                            healths=[health('red', active=1, unassigned=1)])
        with pytest.raises(CommandError, match='still answers.*high watermark'):
            self._load(monkeypatch, client)
        assert client.indices.alias_actions == []
        assert client.indices.deleted == []


class TestADirectoryPerStateToo:
    def test_the_directory_is_everything_before_the_date(self):
        from django_osm_locations.management.commands.locations_import_snapshot import Command

        assert Command.directory_for({'snapshot': 'us-ca-2026-10-18'}) == 'us-ca'
        assert Command.directory_for({'snapshot': 'pl-2026-10-02'}) == 'pl'


class AliasClient:
    """Just enough of a cluster for aliases: which index is under which, and deletes."""

    def __init__(self, aliases):
        self.aliases = {a: set(i) for a, i in aliases.items()}
        self.deleted = []
        client = self

        class Indices:
            def exists_alias(self, name):
                return name in client.aliases

            def get_alias(self, name):
                return {i: {} for i in client.aliases.get(name, ())}

            def update_aliases(self, body):
                for action in body['actions']:
                    if 'remove' in action:
                        client.aliases[action['remove']['alias']].discard(action['remove']['index'])

            def delete(self, index):
                client.deleted.append(index)

        self.indices = Indices()


class TestANewerVersionReplacesTheOlder:
    """Two versions of one part under an alias would answer every search twice."""

    def test_the_older_one_leaves_and_is_deleted(self):
        from django_osm_locations.search.snapshots import retire_older

        client = AliasClient({'locations-streets': {
            'locations-streets-us-ca-20261001000000', 'locations-streets-us-ca-20261018000000'}})
        retired = retire_older(['locations-streets-us-ca-20261018000000'], client=client,
                               prefix='locations')
        assert retired == ['locations-streets-us-ca-20261001000000']
        assert client.aliases['locations-streets'] == {'locations-streets-us-ca-20261018000000'}

    def test_another_state_and_a_country_are_left_alone(self):
        from django_osm_locations.search.snapshots import retire_older

        kept = {'locations-streets-us-tx-20261001000000', 'locations-streets-pl-20261001000000',
                'locations-streets-us-20261001000000'}
        client = AliasClient({'locations-streets': kept | {'locations-streets-us-ca-20261018000000'}})
        assert retire_older(['locations-streets-us-ca-20261018000000'], client=client,
                            prefix='locations') == []
        assert client.deleted == []

    def test_the_country_document_does_not_take_the_states(self):
        """us is a token of its own: its newer version replaces only us."""
        from django_osm_locations.search.snapshots import retire_older

        client = AliasClient({'locations-countries': {
            'locations-countries-us-20261001000000', 'locations-countries-us-ca-20261001000000'}})
        retired = retire_older(['locations-countries-us-20261018000000'], client=client,
                               prefix='locations')
        assert retired == ['locations-countries-us-20261001000000']


class TestWhatAnUndatedNameLoads:
    def test_a_country_is_its_own_directory(self, tmp_path):
        from django_osm_locations.management.commands.locations_import_snapshot import Command

        for name in ('pl', 'us', 'us-ca', 'us-tx', 'usx'):
            (tmp_path / name).mkdir()
        assert Command.group_directories('pl', str(tmp_path)) == ['pl']
        assert Command.group_directories('us', str(tmp_path)) == ['us', 'us-ca', 'us-tx']


class MergeIndices:
    """A cluster whose retention leases lag ``behind`` look-ups before they catch up."""

    def __init__(self, behind=0, shards=None, deleted=0):
        self.behind = behind
        self.shards = shards or {'total': 2, 'successful': 2, 'failed': 0}
        self.deleted = deleted
        self.calls = []

    def stats(self, index, metric=None, params=None):
        if metric == 'docs':
            self.calls.append('docs')
            return {'_all': {'primaries': {'docs': {'count': 3, 'deleted': self.deleted}}}}
        self.calls.append('leases')
        retaining = 2 if self.behind > 0 else 3
        self.behind -= 1
        return {'indices': {index: {'shards': {'0': [{
            'seq_no': {'max_seq_no': 2},
            'retention_leases': {'leases': [{'retaining_seq_no': retaining}]}}]}}}}

    def forcemerge(self, index, params):
        self.calls.append(('forcemerge', index, params))
        #: whether the client would have asked again on a timeout
        self.retried_on_timeout = self.transport.retry_on_timeout
        return {'task': 'node:1'}

    def refresh(self, index):
        self.calls.append('refresh')


class MergeTasks:
    def __init__(self, indices, polls=1, lost=False):
        self.indices, self.polls, self.gets, self.lost = indices, polls, 0, lost

    def get(self, task_id):
        from opensearchpy.exceptions import NotFoundError

        self.gets += 1
        if self.lost:
            raise NotFoundError(404, 'resource_not_found_exception', 'task [node:1] isn\'t running')
        if self.gets < self.polls:
            return {'completed': False}
        return {'completed': True, 'response': {'_shards': self.indices.shards}}


class MergeClient:
    def __init__(self, polls=1, lost=False, **kwargs):
        self.transport = FakeTransport()
        self.indices = MergeIndices(**kwargs)
        self.indices.transport = self.transport
        self.tasks = MergeTasks(self.indices, polls, lost)


class TestMergingBeforeTheSnapshot:
    """
    No document written again may linger, deleted, in the term statistics a
    published index is scored by: Croatia's addresses kept 98,663 after their
    repairs, and "Ulica 5 Karlovac" fell from first to fourth.
    """

    def test_it_waits_for_the_leases_first(self):
        """A merge keeps what a lease still covers, and a lease lags the last write by ~30 s."""
        client = MergeClient(behind=2)
        snapshots.merge(['i'], client=client, interval=0)
        assert client.indices.calls[:4] == ['leases', 'leases', 'leases',
                                            ('forcemerge', 'i', {'max_num_segments': 1,
                                                                 'wait_for_completion': 'false'})]

    def test_it_merges_as_a_task_and_polls_it(self):
        """A country's addresses take longer to merge than the client waits for an answer."""
        client = MergeClient(polls=3)
        snapshots.merge(['a', 'b'], client=client, interval=0)
        assert client.tasks.gets == 3
        assert ('forcemerge', 'a,b', {'max_num_segments': 1, 'wait_for_completion': 'false'}) \
            in client.indices.calls

    def test_leases_that_do_not_catch_up_stop_it_before_any_merge(self):
        """Merged then, a country's addresses would take hours to keep what this is for."""
        client = MergeClient(behind=10 ** 6)
        with pytest.raises(RuntimeError, match='retention leases'):
            snapshots.merge(['i'], client=client, interval=0, lease_timeout=0)
        assert not any(isinstance(call, tuple) for call in client.indices.calls)

    def test_the_merge_is_asked_for_once(self):
        """A retry after a timeout would start a second merge beside the first."""
        client = MergeClient()
        snapshots.merge(['i'], client=client, interval=0)
        assert client.indices.retried_on_timeout is False
        assert client.transport.retry_on_timeout is True

    def test_a_copy_with_no_lease_holds_nothing_back(self):
        """filter_path leaves out retention_leases when a copy has none."""
        client = MergeClient()
        client.indices.stats = lambda index, metric=None, params=None: (
            {'_all': {'primaries': {'docs': {'count': 3, 'deleted': 0}}}} if metric == 'docs'
            else {'indices': {index: {'shards': {'0': [{'seq_no': {'max_seq_no': 2}}]}}}})
        snapshots.merge(['i'], client=client, interval=0, lease_timeout=0)
        assert any(isinstance(call, tuple) for call in client.indices.calls)

    def test_a_task_the_cluster_forgot_stops_it_as_an_error(self):
        """Restarted under the merge, the cluster no longer knows the task."""
        client = MergeClient(lost=True)
        with pytest.raises(RuntimeError, match='followed to its end'):
            snapshots.merge(['i'], client=client, interval=0)

    def test_a_shard_that_failed_to_merge_stops_it(self):
        """OpenSearch reports it inside an answer that is a success."""
        client = MergeClient(shards={'total': 2, 'successful': 1, 'failed': 1})
        with pytest.raises(RuntimeError, match='every shard'):
            snapshots.merge(['i'], client=client, interval=0)

    def test_no_shard_at_all_stops_it(self):
        client = MergeClient(shards={'total': 0, 'successful': 0, 'failed': 0})
        with pytest.raises(RuntimeError, match='every shard'):
            snapshots.merge(['i'], client=client, interval=0)

    def test_a_deleted_document_left_stops_it(self):
        client = MergeClient(deleted=4)
        with pytest.raises(RuntimeError, match='4 deleted documents'):
            snapshots.merge(['i'], client=client, interval=0)
        assert client.indices.calls[-2:] == ['refresh', 'docs']

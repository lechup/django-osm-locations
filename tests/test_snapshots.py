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

    def exists_alias(self, name):
        return name in self._aliases

    def get_alias(self, name):
        return {index: {} for index in self._aliases.get(name, [])}

    def exists(self, index):
        return index in self._existing

    def update_aliases(self, body):
        self.alias_actions.extend(body['actions'])


class FakeSnapshot:
    def __init__(self, states=None):
        self.created = []
        self.restored = []
        self.repositories = []
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

    def create(self, repository, snapshot, body, params):
        self.created.append((snapshot, body, params))

    def restore(self, repository, snapshot, body, params):
        self.restored.append((snapshot, body, params))


class FakeClient:
    def __init__(self, aliases=None, existing=()):
        self.indices = FakeIndices(aliases, existing)
        self.snapshot = FakeSnapshot()


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

"""
Which search indexes are old enough to go.

Retention is per location type and per country. Sorting names across countries
put Denmark before Poland, so "keep the newest superseded index" kept Poland's
rollback and removed Denmark's.
"""
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from django_osm_locations.management.commands.locations_index_cleanup import (
    ALL, LIVE, PENDING, ROLLBACK, STALE, UNKNOWN, plan,
)

STEM = 'locations-streets-'


def name(stamp, country=None):
    return STEM + ('{}-'.format(country) if country else '') + stamp


def statuses(names, live, keep=1):
    return {row[0]: row[3] for row in plan(names, live, STEM, keep)}


class TestPerCountry:
    def test_each_country_keeps_its_own_rollback(self):
        pl_old, pl_prev, pl_live = (name(s, 'pl') for s in (
            '20260928000000', '20260929000000', '20260930000000'))
        dk_prev, dk_live = (name(s, 'dk') for s in ('20260929000000', '20260930000000'))
        got = statuses([pl_old, pl_prev, pl_live, dk_prev, dk_live], [pl_live, dk_live])
        assert got == {
            pl_old: STALE, pl_prev: ROLLBACK, pl_live: LIVE,
            dk_prev: ROLLBACK, dk_live: LIVE,
        }

    def test_the_all_countries_generation_is_a_group_of_its_own(self):
        old, live = name('20260920000000'), name('20260923000000')
        pl_live = name('20260930000000', 'pl')
        rows = plan([old, live, pl_live], [live, pl_live], STEM, 0)
        assert {r[0]: (r[1], r[3]) for r in rows} == {
            old: (ALL, STALE), live: (ALL, LIVE), pl_live: ('pl', LIVE),
        }


class TestWhatIsNeverRemoved:
    def test_an_aliased_index_whatever_its_age(self):
        oldest = name('20260101000000', 'pl')
        newer = name('20260930000000', 'pl')
        assert statuses([oldest, newer], [oldest, newer], keep=0)[oldest] == LIVE

    def test_a_build_newer_than_the_live_one_awaiting_promotion(self):
        live, pending = name('20260929000000', 'pl'), name('20260930000000', 'pl')
        assert statuses([live, pending], [live], keep=0)[pending] == PENDING

    def test_a_name_that_does_not_parse(self):
        odd = STEM + 'pl-backup'
        assert statuses([odd], [], keep=0)[odd] == UNKNOWN

    def test_a_group_with_nothing_live_keeps_its_newest(self):
        """An unpromoted first build, or a country whose alias was removed."""
        a, b = name('20260929000000', 'se'), name('20260930000000', 'se')
        assert statuses([a, b], []) == {a: STALE, b: ROLLBACK}


class TestKeep:
    @pytest.mark.parametrize('keep,stale', [(0, 3), (1, 2), (2, 1), (5, 0)])
    def test_how_many_superseded_survive(self, keep, stale):
        stamps = ['2026092{}000000'.format(d) for d in range(1, 5)]
        names = [name(s, 'pl') for s in stamps]
        got = statuses(names, [names[-1]], keep=keep)
        assert list(got.values()).count(STALE) == stale


class FakeIndices:
    def __init__(self, existing, live):
        self.existing, self.live, self.deleted = existing, live, []

    def delete(self, index):
        self.deleted.append(index)


class FakeReindexer:
    prefix = 'locations'

    def __init__(self, existing, live):
        self.index = mock.Mock()
        self.index.name = 'streets'
        self.index.index_name = lambda prefix, suffix: '{}-streets-{}'.format(prefix, suffix)
        self.client = mock.Mock()
        self.client.indices = FakeIndices(existing, live)
        self.client.cat.indices.return_value = [
            {'index': n, 'store.size': '2048'} for n in existing]
        self.client.count.return_value = {'count': 10}

    def existing_indexes(self):
        return self.client.indices.existing

    def aliased_indexes(self):
        return self.client.indices.live


class TestCommand:
    def run(self, *args):
        old, prev, live = (name(s, 'pl') for s in (
            '20260928000000', '20260929000000', '20260930000000'))
        fake = FakeReindexer([old, prev, live], [live])
        out = StringIO()
        with mock.patch(
                'django_osm_locations.management.commands.locations_index_cleanup.reindexers',
                return_value=[fake]):
            call_command('locations_index_cleanup', *args, stdout=out)
        return fake.client.indices.deleted, out.getvalue(), old

    def test_it_only_reports_by_default(self):
        deleted, out, old = self.run()
        assert deleted == []
        assert 'would remove 1 indexes' in out and old in out

    def test_list_removes_nothing(self):
        deleted, out, _ = self.run('--list')
        assert deleted == [] and 'would remove' not in out

    def test_delete_removes_only_the_stale(self):
        deleted, out, old = self.run('--delete')
        assert deleted == [old]
        assert 'removed 1 indexes' in out

    def test_a_country_filter(self):
        deleted, out, _ = self.run('--country', 'dk', '--delete')
        assert deleted == [] and 'nothing to remove' in out

    def test_delete_and_list_together_is_refused(self):
        with pytest.raises(CommandError):
            self.run('--delete', '--list')


class FakeAliasIndices(FakeIndices):
    """Alias updates applied to the fake's state, so a swap can be run twice."""

    def __init__(self, existing, live):
        super().__init__(existing, live)
        self.calls = []

    def update_aliases(self, body):
        self.calls.append(body['actions'])
        for action in body['actions']:
            if 'remove' in action:
                self.live.remove(action['remove']['index'])
            else:
                self.live.append(action['add']['index'])


class TestRollback:
    PL = [name(s, 'pl') for s in ('20260928000000', '20260929000000', '20260930000000')]
    DK = [name(s, 'dk') for s in ('20260929000000', '20260930000000')]

    def fake(self):
        fake = FakeReindexer(self.PL + self.DK, [self.PL[-1], self.DK[-1]])
        fake.alias = 'locations-streets'
        fake.client.indices = FakeAliasIndices(self.PL + self.DK, [self.PL[-1], self.DK[-1]])
        return fake

    def run(self, fake, *args):
        out = StringIO()
        with mock.patch(
                'django_osm_locations.management.commands.locations_index_cleanup.reindexers',
                return_value=[fake]):
            call_command('locations_index_cleanup', *args, stdout=out)
        return out.getvalue()

    def test_it_only_reports_without_apply(self):
        fake = self.fake()
        out = self.run(fake, '--rollback', '--country', 'pl')
        assert fake.client.indices.calls == []
        assert 'on   ' + self.PL[1] in out and 'off  ' + self.PL[2] in out

    def test_apply_swaps_that_country_only(self):
        fake = self.fake()
        self.run(fake, '--rollback', '--country', 'pl', '--apply')
        assert sorted(fake.client.indices.live) == sorted([self.PL[1], self.DK[-1]])

    def test_forward_undoes_it(self):
        fake = self.fake()
        self.run(fake, '--rollback', '--country', 'pl', '--apply')
        self.run(fake, '--forward', '--country', 'pl', '--apply')
        assert sorted(fake.client.indices.live) == sorted([self.PL[2], self.DK[-1]])

    def test_rollback_again_steps_further_back(self):
        fake = self.fake()
        self.run(fake, '--rollback', '--country', 'pl', '--apply')
        self.run(fake, '--rollback', '--country', 'pl', '--apply')
        assert sorted(fake.client.indices.live) == sorted([self.PL[0], self.DK[-1]])

    def test_forward_with_nothing_newer_changes_nothing(self):
        fake = self.fake()
        out = self.run(fake, '--forward', '--country', 'pl', '--apply')
        assert fake.client.indices.calls == [] and 'nothing to switch' in out

    def test_every_type_goes_in_one_alias_update(self):
        fake = self.fake()
        self.run(fake, '--rollback', '--country', 'pl', '--apply')
        assert len(fake.client.indices.calls) == 1

    def test_a_country_is_required(self):
        with pytest.raises(CommandError, match='--country'):
            self.run(self.fake(), '--rollback')

    def test_apply_alone_is_refused(self):
        with pytest.raises(CommandError, match='--apply goes with'):
            self.run(self.fake(), '--apply', '--country', 'pl')

    def test_nothing_to_roll_back_to(self):
        fake = self.fake()
        out = self.run(fake, '--rollback', '--country', 'se', '--apply')
        assert fake.client.indices.calls == [] and 'nothing to switch' in out

"""
Turning progress bars off.

A bar is a stream of carriage returns: right on a terminal, unreadable in a log
file, and in cron it is stderr output from a job that had nothing to say, which
is how a nightly import starts sending mail.
"""
import io
import sys
from unittest import mock

import pytest

from django_osm_locations.utils.progress import progress, show_progress_default
from django_osm_locations.management.commands.locations_import import Command as ImportCommand


def rendered(iterable, **kwargs):
    """Whatever the bar wrote to stderr while consuming the iterable."""
    captured = io.StringIO()
    with mock.patch.object(sys, 'stderr', captured):
        list(progress(iterable, **kwargs))
    return captured.getvalue()


class TestExplicitControl:
    def test_show_false_writes_nothing(self):
        assert rendered(range(50), total=50, show=False) == ''

    def test_show_true_writes_a_bar(self):
        assert rendered(range(50), total=50, show=True) != ''

    def test_values_pass_through_either_way(self):
        for show in (True, False, None):
            assert list(progress([1, 2, 3], total=3, show=show)) == [1, 2, 3]


class TestTerminalDetection:
    def test_nothing_is_drawn_when_stderr_is_redirected(self):
        """The log-file and cron case: output goes somewhere that is not a tty."""
        assert rendered(range(50), total=50) == ''

    def test_setting_can_force_bars_on(self, settings):
        settings.LOCATIONS_SHOW_PROGRESS = True
        assert rendered(range(50), total=50) != ''

    def test_setting_can_force_bars_off(self, settings):
        settings.LOCATIONS_SHOW_PROGRESS = False
        assert rendered(range(50), total=50) == ''

    def test_default_is_to_decide_from_the_terminal(self, settings):
        if hasattr(settings, 'LOCATIONS_SHOW_PROGRESS'):
            del settings.LOCATIONS_SHOW_PROGRESS
        assert show_progress_default() is None


class TestCommandFlags:
    @pytest.mark.parametrize('options,expected', [
        ({'verbosity': 1}, None),                      # decide from the terminal
        ({'verbosity': 0}, False),                     # -v 0
        ({'verbosity': 1, 'no_progress': True}, False),
        ({'verbosity': 2, 'no_progress': False}, None),
        ({}, None),
    ])
    def test_show_progress_from_options(self, options, expected):
        assert ImportCommand.show_progress(options) is expected

    def test_reindex_honours_the_same_flags(self):
        from django_osm_locations.management.commands.locations_reindex import (
            Command as ReindexCommand,
        )
        assert ReindexCommand._show_progress({'verbosity': 0}) is False
        assert ReindexCommand._show_progress({'no_progress': True}) is False
        assert ReindexCommand._show_progress({'verbosity': 1}) is None


class TestEverythingUsesTheHelper:
    """
    Every bar has to go through the helper, or a redirected run still gets
    carriage returns from whichever one was missed. The data migrations count:
    they run on `migrate` in CI and cron too.
    """

    def test_no_module_calls_tqdm_directly(self):
        import pathlib

        import django_osm_locations

        root = pathlib.Path(django_osm_locations.__file__).parent
        offenders = []
        for path in root.rglob('*.py'):
            if path.name == 'progress.py':
                continue
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                stripped = line.strip()
                if 'tqdm(' in stripped and 'tqdm.write(' not in stripped:
                    offenders.append('{}:{}'.format(path.relative_to(root), number))
        assert not offenders, 'these draw bars without the helper: {}'.format(offenders)

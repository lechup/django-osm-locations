"""
Resuming a recalculation, and matching by place before matching by name.

The settlement step matched addresses to streets by name first, which pairs
each street with every address of that name in the country -- 393 million
pairs for Polna alone, 5.7 billion planner cost units for the step against
27.9 million once a small box narrows it through the GiST index. Killing it to
swap the query in used to mean repeating two hours of phases that had already
committed; --from resumes on the same code path instead.
"""
import math
from unittest import mock

import pytest
from django.core.management.base import CommandError

from django_osm_locations.management.commands.locations_recalculate_all import (
    Command as RecalculateAll,
)
from django_osm_locations.management.commands.locations_recalculate_denormalized_fields import (
    Command as Denorm,
)

EFFECTFUL = (
    'clean_denorm_fields_before_update', 'update_street_place_field',
    'inherit_street_ancestry_from_place', 'inherit_address_ancestry_from_place',
    'update_address_street_field', 'clean_denorm_fields_after_update',
    'update_address_slugs',
)


def run(**options):
    """Run the walk with every side effect replaced by a recorder."""
    called = []
    patches = [mock.patch(
        'django_osm_locations.management.commands.locations_recalculate_all.call_command',
        side_effect=lambda name, *a, **kw: called.append(
            '{}:{}'.format(name.replace('locations_recalculate_', ''), a[0] if a else '')),
    )]
    for name in EFFECTFUL:
        patches.append(mock.patch.object(
            Denorm, name, side_effect=lambda n=name: called.append(n)))
    command = RecalculateAll()
    command.report_write_amplification = lambda: None
    for p in patches:
        p.start()
    try:
        command.handle(**options)
    finally:
        for p in patches:
            p.stop()
    return called


class TestResuming:
    def test_everything_runs_without_it(self):
        called = run()
        assert called[0] == 'areas_and_lengths:'
        assert 'clean_denorm_fields_before_update' in called

    def test_earlier_phases_are_skipped(self):
        called = run(start_from='streets without a place')
        assert 'areas_and_lengths:' not in called
        assert 'clean_denorm_fields_before_update' not in called, (
            'cleaning would wipe everything the skipped phases committed')
        assert called[0] == 'update_street_place_field'

    def test_everything_after_the_phase_still_runs_in_order(self):
        full = run()
        resumed = run(start_from='streets without a place')
        assert resumed == full[full.index('update_street_place_field'):]

    def test_a_phase_inside_the_level_loop_can_be_named(self):
        called = run(start_from='slugs: Street')
        assert called[0] == 'slugs:Street'

    def test_a_name_that_matches_nothing_is_an_error(self):
        """A typo must not quietly skip every phase and report success."""
        with pytest.raises(CommandError, match='No phase called'):
            run(start_from='streets without a plcae')


class TestTheMatchBox:
    def test_both_name_matches_look_in_the_box_first(self):
        for sql in (Denorm.get_sql_for_street_settlement_from_addresses(),
                    Denorm.get_sql_for_address_street_link()):
            assert 'ST_Expand' in sql
            assert sql.index('ST_Expand') < sql.index('street_name')

    def test_each_still_takes_exactly_one_parameter(self):
        """
        The box is formatted in, the distance stays bound. psycopg2 counts
        placeholders across the whole string, comments included.
        """
        for sql in (Denorm.get_sql_for_street_settlement_from_addresses(),
                    Denorm.get_sql_for_address_street_link()):
            assert sql.count('%s') == 1

    @pytest.mark.parametrize('latitude', [0, 45, 55, 60, 71])
    def test_the_box_never_drops_a_match_within_the_radius(self, latitude):
        """
        The box must hold ADDRESS_MATCH_METRES in every direction at every
        latitude this package imports, up to the top of Norway.
        """
        metres_per_degree_lat = 111_320
        metres_per_degree_lon = 111_320 * math.cos(math.radians(latitude))
        box = Denorm.MATCH_BOX_DEGREES
        assert box * metres_per_degree_lat > Denorm.ADDRESS_MATCH_METRES
        assert box * metres_per_degree_lon > Denorm.ADDRESS_MATCH_METRES

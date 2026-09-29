"""
Slugs and ancestry, and the order they have to run in.

A duplicate slug is qualified by an ancestor's slug, and the denormalised
arrays are where ancestors' slugs are stored, so the two commands feed each
other. Running them as whole passes means one has to run twice; walking the
hierarchy top-down means each level is written once. These tests pin the walk
and the batching that made it affordable -- resolving slugs row by row through
``save()`` re-derived GEOS geometry per row, which measured ~3 updates a second
against 2.5 million duplicate streets.
"""
from unittest import mock

import pytest

from django_osm_locations.management.commands.locations_recalculate_all import (
    Command as RecalculateAll,
)
from django_osm_locations.management.commands.locations_recalculate_denormalized_fields import (
    Command as Denorm,
)
from django_osm_locations.management.commands.locations_recalculate_slugs import (
    Command as Slugs,
)
from django_osm_locations.models import Country, CountrySubarea, Place, PlaceSubarea, Street


class TestWalkOrder:
    def _calls(self):
        calls = []
        with mock.patch(
            'django_osm_locations.management.commands.locations_recalculate_all.call_command',
            side_effect=lambda name, *a, **kw: calls.append((name, a)),
        ), mock.patch.object(Denorm, 'clean_denorm_fields_before_update'), \
                mock.patch.object(Denorm, 'inherit_address_ancestry_from_place'), \
                mock.patch.object(Denorm, 'inherit_street_ancestry_from_place'), \
                mock.patch.object(Denorm, 'update_address_street_field'), \
                mock.patch.object(Denorm, 'update_address_slugs'), \
                mock.patch.object(Denorm, 'clean_denorm_fields_after_update'), \
                mock.patch.object(Denorm, 'update_street_place_field'), \
                mock.patch.object(Denorm, 'update_search_vector_field'):
            RecalculateAll().handle()
        return calls

    def test_every_ancestor_is_pushed_down_exactly_once(self):
        pushed = [a[0] for name, a in self._calls()
                  if name == 'locations_recalculate_denormalized_fields']
        assert pushed == ['Country', 'CountrySubarea', 'Place', 'PlaceSubarea']

    def test_a_level_is_slugged_before_it_is_pushed_down(self):
        """The whole point: an ancestor's slug is final before anything copies it."""
        calls = self._calls()
        for level in ('Country', 'CountrySubarea', 'Place', 'PlaceSubarea'):
            slugged = calls.index(('locations_recalculate_slugs', (level,)))
            pushed = calls.index(('locations_recalculate_denormalized_fields', (level,)))
            assert slugged < pushed, '{} pushed down before its slugs settled'.format(level)

    def test_streets_are_slugged_last(self):
        names = [a[0] for name, a in self._calls() if name == 'locations_recalculate_slugs']
        assert names == ['Country', 'CountrySubarea', 'Place', 'PlaceSubarea', 'Street']

    def test_areas_come_first(self):
        assert self._calls()[0][0] == 'locations_recalculate_areas_and_lengths'

    def test_the_search_vectors_are_not_rebuilt(self):
        """
        Nothing queries them: search moved to OpenSearch and an edge n-gram
        analyser does the prefix matching the tsvector columns used to. They
        cost about two hours of every recalculation, most of it a single
        UPDATE over 2.8 million streets.
        """
        with mock.patch.object(Denorm, 'update_search_vector_field') as build, \
                mock.patch.object(Denorm, 'inherit_address_ancestry_from_place'), \
                mock.patch.object(Denorm, 'inherit_street_ancestry_from_place'), \
                mock.patch(
                    'django_osm_locations.management.commands.'
                    'locations_recalculate_all.call_command'
                ), mock.patch.object(Denorm, 'clean_denorm_fields_before_update'), \
                mock.patch.object(Denorm, 'update_street_place_field'), \
                mock.patch.object(Denorm, 'update_address_street_field'), \
                mock.patch.object(Denorm, 'update_address_slugs'), \
                mock.patch.object(Denorm, 'clean_denorm_fields_after_update'):
            RecalculateAll().handle()

        build.assert_not_called()

    def test_the_arrays_are_cleaned_once_and_never_stripped(self):
        """
        Emptied once before the walk. There is no strip after it any more: the
        walk appends two-dimensional rows, so it never writes a placeholder
        that would need taking out -- a pass that cost 1h28m.
        """
        with mock.patch.object(Denorm, 'clean_denorm_fields_before_update') as before, \
                mock.patch.object(Denorm, 'inherit_address_ancestry_from_place'), \
                mock.patch.object(Denorm, 'inherit_street_ancestry_from_place'), \
                mock.patch.object(Denorm, 'update_address_street_field'), \
                mock.patch.object(Denorm, 'update_address_slugs'), \
                mock.patch.object(Denorm, 'clean_denorm_fields_after_update') as after, \
                mock.patch(
                    'django_osm_locations.management.commands.'
                    'locations_recalculate_all.call_command'
                ), mock.patch.object(Denorm, 'update_street_place_field'), \
                mock.patch.object(Denorm, 'update_search_vector_field'):
            RecalculateAll().handle()

        assert before.call_count == 1
        assert after.call_count == 0


class TestHeavyFieldsAreDeferred:
    @pytest.mark.parametrize('model_class,expected', [
        (Street, ('lines', 'search_vector', 'search_vector_name')),
        (Place, ('border', 'search_vector', 'search_vector_name')),
        (Country, ('border', 'search_vector', 'search_vector_name')),
    ])
    def test_geometry_and_tsvectors_are_left_behind(self, model_class, expected):
        assert Slugs._heavy_fields(model_class) == expected

    def test_center_is_kept(self):
        """Place qualifies by the nearest larger place, which needs its center."""
        assert 'center' not in Slugs._heavy_fields(Place)


class TestSelectedModelClasses:
    def test_defaults_to_every_ancestor(self):
        assert Denorm.selected_model_classes([]) == [
            Country, CountrySubarea, Place, PlaceSubarea
        ]

    def test_picks_by_name_case_insensitively(self):
        assert Denorm.selected_model_classes(['place']) == [Place]

    def test_a_street_is_not_a_source_for_the_geometric_pass(self):
        """
        It has lines, not a border, and a point is not inside a line. An
        address gets its street by name instead.
        """
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match='not an ancestor model'):
            Denorm.selected_model_classes(['Street'])

    def test_an_address_is_not_an_ancestor(self):
        """Nothing sits below a house number."""
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match='not an ancestor model'):
            Denorm.selected_model_classes(['Address'])


@pytest.mark.django_db
class TestSlugsAreWrittenInBatches:
    """
    Resolving a duplicate must not go through ``Model.save()``.

    ``save()`` runs every ``_presave_*`` hook, so a slug-only change rebuilt
    the bbox and reprojected the border -- GEOS work per row, for a column the
    hooks never touch. With 2.5 million duplicate streets that measured about
    three updates a second.
    """

    @staticmethod
    def _streets(count):
        from django.contrib.gis.geos import LineString, MultiLineString

        for i in range(count):
            Street.objects.create(
                name='Station Road',
                lines=MultiLineString(LineString((i, i), (i + 0.1, i + 0.1))),
                place=[['Town {}'.format(i), 'town-{}'.format(i)]],
            )

    def test_save_is_never_called_while_uniquifying(self):
        self._streets(4)
        with mock.patch.object(Street, 'save', side_effect=AssertionError('save() called')):
            Slugs.set_unique_slugs(Street)
        assert Street.objects.filter(slug='station-road').count() == 1

    def test_duplicates_are_still_qualified(self):
        """One keeps the bare slug -- which one is _rank's business -- and the
        rest are qualified by the town they are actually in."""
        self._streets(3)
        Slugs.set_unique_slugs(Street)

        slugs = dict(Street.objects.values_list('id', 'slug'))
        assert sorted(slugs.values()).count('station-road') == 1
        for street in Street.objects.all():
            if street.slug != 'station-road':
                assert street.slug == 'station-road-{}'.format(street.place[0][1])

    def test_writes_are_batched(self):
        self._streets(5)
        with mock.patch.object(Slugs, 'BATCH_SIZE', 2), \
                mock.patch.object(
                    Street.objects, 'bulk_update', wraps=Street.objects.bulk_update
                ) as bulk:
            Slugs.set_unique_slugs(Street)
        # four rows to rewrite, two at a time
        assert [len(call.args[0]) for call in bulk.call_args_list] == [2, 2]


class TestCleanupIsOnePassPerTable:
    """
    A street has four denormalised columns, and cleaning them one at a time
    rewrote all 2.8 million rows four times over -- about twenty minutes each,
    the slowest phase in the pipeline. The work per column is trivial; it was
    the repeated full-table rewrite that cost.
    """

    def test_every_column_is_set_in_one_statement(self):
        from django_osm_locations.models import Street

        sql = Denorm.get_sql_for_clean_denorm_fields('street', Street.denorm_fields)

        assert sql.count('UPDATE') == 1
        for field in Street.denorm_fields:
            assert '{} = CASE'.format(field) in sql

    def test_a_column_without_the_placeholder_is_left_alone(self):
        """Otherwise the CASE would truncate a real first ancestor."""
        sql = Denorm.get_sql_for_clean_denorm_fields('street', ('country',))
        assert 'ELSE country END' in sql

    def test_rows_with_no_placeholder_anywhere_are_not_rewritten(self):
        from django_osm_locations.models import Street

        sql = Denorm.get_sql_for_clean_denorm_fields('street', Street.denorm_fields)
        where = sql.split('WHERE', 1)[1]
        for field in Street.denorm_fields:
            assert field in where

"""
Streets that fall inside no place.

A street gets a place from the point-in-polygon pass only if it lies inside
one's border, and 771,253 of the live Polish and British streets lie inside
none -- rural roads, trunk roads between towns. They keep their country and
country subarea, so they remain findable, but they have no town in their
ancestry and nothing to qualify a duplicate slug against.
"""
import pytest

from django_osm_locations.management.commands.locations_recalculate_denormalized_fields import (
    Command as Denorm,
)


class TestNearestPlaceSql:
    def test_only_streets_without_a_place_are_touched(self):
        sql = Denorm.get_sql_for_street_place_field()
        assert 'array_length(s2.place, 1) IS NULL' in sql

    def test_it_uses_the_knn_operator(self):
        """`<->` walks the GiST index; ORDER BY ST_Distance would scan."""
        assert 'ORDER BY center <-> s2.center' in Denorm.get_sql_for_street_place_field()

    def test_it_is_one_statement_not_a_loop(self):
        """
        The Python version did a query and a save() per street -- about three
        rows a second, some seventy hours for the streets that need it, which
        is presumably why it was written and never called.
        """
        sql = Denorm.get_sql_for_street_place_field()
        assert sql.count('UPDATE') == 1
        assert 'CROSS JOIN LATERAL' in sql

    def test_merged_places_are_not_offered(self):
        """They are duplicates the import already resolved, and unindexed."""
        assert 'NOT is_merged' in Denorm.get_sql_for_street_place_field()

    def test_the_search_is_bounded(self):
        sql = Denorm.get_sql_for_street_place_field()
        assert 'ST_DWithin' in sql

    def test_the_bound_matches_the_radius_used_elsewhere(self):
        """Place.near_bbox already reaches 25km."""
        assert Denorm.NEAREST_PLACE_KM == 25


class TestItIsActuallyCalled:
    def test_the_pass_runs_after_the_arrays_are_cleaned(self, monkeypatch):
        """
        Only meaningful once every place has been pushed down and the
        placeholder element stripped -- and it was never called at all before.
        """
        calls = []
        monkeypatch.setattr(Denorm, 'clean_denorm_fields_before_update',
                            classmethod(lambda cls: calls.append('clean_before')))
        monkeypatch.setattr(Denorm, 'update_denorm_fields_for_model_class',
                            classmethod(lambda cls, model, **kwargs: None))
        monkeypatch.setattr(Denorm, 'clean_denorm_fields_after_update',
                            classmethod(lambda cls: calls.append('clean_after')))
        monkeypatch.setattr(Denorm, 'update_street_place_field',
                            classmethod(lambda cls: calls.append('street_place')))
        monkeypatch.setattr(Denorm, 'update_search_vector_field',
                            classmethod(lambda cls: None))

        Denorm().handle(types=[], no_clean_before=False, no_clean_after=False,
                        no_search_vector=True)

        assert calls == ['clean_before', 'clean_after', 'street_place']

    def test_it_is_skipped_when_the_caller_cleans_for_itself(self, monkeypatch):
        """The top-down walk cleans once around the whole thing."""
        calls = []
        monkeypatch.setattr(Denorm, 'update_denorm_fields_for_model_class',
                            classmethod(lambda cls, model, **kwargs: None))
        monkeypatch.setattr(Denorm, 'update_street_place_field',
                            classmethod(lambda cls: calls.append('street_place')))
        monkeypatch.setattr(Denorm, 'update_search_vector_field',
                            classmethod(lambda cls: None))

        Denorm().handle(types=[], no_clean_before=True, no_clean_after=True,
                        no_search_vector=True)

        assert calls == []


class TestTheWalkRunsItInTimeToMatter:
    """
    The slug pass qualifies a duplicate street by its place, so attaching the
    strays has to happen before streets are slugged. Running it after the walk
    -- where it first sat -- left 771,321 streets slugged with no place to
    qualify by, and the OSM-id fallback rate unmoved at 27.7% through a
    twelve-hour recalculation.
    """

    def _order(self):
        from unittest import mock

        from django_osm_locations.management.commands.locations_recalculate_all import (
            Command as RecalculateAll,
        )

        order = []
        with mock.patch(
            'django_osm_locations.management.commands.locations_recalculate_all.call_command',
            side_effect=lambda name, *a, **kw: order.append(
                '{}:{}'.format(name.replace('locations_recalculate_', ''), a[0] if a else '')
            ),
        ), mock.patch.object(Denorm, 'clean_denorm_fields_before_update'), \
                mock.patch.object(Denorm, 'inherit_address_ancestry_from_place'), \
                mock.patch.object(Denorm, 'inherit_street_ancestry_from_place'), \
                mock.patch.object(Denorm, 'update_address_street_field'), \
                mock.patch.object(Denorm, 'update_address_slugs'), \
                mock.patch.object(Denorm, 'clean_denorm_fields_after_update'), \
                mock.patch.object(Denorm, 'update_street_place_field',
                                  side_effect=lambda: order.append('attach')):
            RecalculateAll().handle()
        return order

    def test_it_runs_at_all(self):
        assert 'attach' in self._order()

    def test_it_runs_before_streets_are_slugged(self):
        order = self._order()
        assert order.index('attach') < order.index('slugs:Street')

    def test_it_runs_after_places_have_been_pushed_down(self):
        """There is nothing to be nearest to until places are in the arrays."""
        order = self._order()
        assert order.index('denormalized_fields:Place') < order.index('attach')


class TestItToleratesThePlaceholder:
    def test_an_array_holding_only_the_placeholder_counts_as_empty(self):
        """
        Mid-walk the arrays still carry ['','']; a street with no place looks
        like [['','']] rather than {}.
        """
        sql = Denorm.get_sql_for_street_place_field()
        assert "ARRAY[ARRAY['','']]::varchar[]" in sql

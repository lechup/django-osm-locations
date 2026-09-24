"""
Extension points for country-specific work.

Countries differ in more than which admin_level means what: Poland has to
collapse a county that is really the city inside it, and the UK's constituent
countries may yet need reshaping after import. Before these hooks, that meant
overriding do_import or do_merge wholesale and remembering to call super().
"""
from unittest import mock

import pytest

from django_osm_locations.management.commands.importers.base import BaseImporter
from django_osm_locations.management.commands.importers.poland import PolandImporter


class TestDefaults:
    @pytest.mark.parametrize('hook', ['before_import', 'after_import',
                                      'before_merge', 'after_merge'])
    def test_every_hook_exists_and_does_nothing(self, hook):
        importer = BaseImporter.__new__(BaseImporter)
        assert getattr(importer, hook)() is None

    def test_an_importer_that_overrides_nothing_still_runs(self):
        """No chaining, no super() to forget."""
        from django_osm_locations.management.commands.importers.denmark import DenmarkImporter

        for hook in ('before_import', 'after_import', 'before_merge', 'after_merge'):
            assert getattr(DenmarkImporter, hook) is getattr(BaseImporter, hook)


@pytest.mark.django_db
class TestImportHooks:
    # do_import wraps each phase in transaction.atomic(), which needs a database
    @pytest.fixture
    def importer(self):
        instance = BaseImporter.__new__(BaseImporter)
        instance.relations, instance.ways, instance.nodes = {}, {}, {}
        instance.show_progress = False
        instance.country_border = None
        instance.country_boundary = None
        instance.writers = {}
        instance.country = mock.MagicMock()
        # do_import parses the extract first; there is no extract here
        instance._parse_and_cache = lambda: None
        # reads the extract a second time; there is no extract here
        instance.import_addresses = lambda: None
        return instance

    def test_before_import_runs_before_anything_is_written(self, importer):
        order = []
        importer.before_import = lambda: order.append('before')
        importer.after_import = lambda: order.append('after')
        importer._import_relations = lambda: order.append('relations')
        importer._import_ways = lambda: order.append('ways')
        importer._import_nodes = lambda: order.append('nodes')
        importer._flush_writes = lambda: None
        importer.do_update_country_border = lambda: order.append('border')
        importer.import_addresses = lambda: order.append('addresses')

        importer.do_import()

        # addresses come after the border because each one is filtered
        # against it as it arrives
        assert order == ['before', 'relations', 'ways', 'nodes', 'border',
                         'addresses', 'after']

    def test_after_import_sees_the_country_border(self, importer):
        """It runs after do_update_country_border, so the border is settled."""
        seen = []
        importer._import_relations = importer._import_ways = lambda: None
        importer._import_nodes = importer._flush_writes = lambda: None
        importer.do_update_country_border = lambda: seen.append('border set')
        importer.after_import = lambda: seen.append('hook')

        importer.do_import()

        assert seen == ['border set', 'hook']


class TestMergeHooks:
    @pytest.fixture
    def importer(self):
        instance = BaseImporter.__new__(BaseImporter)
        instance.show_progress = False
        instance.country = mock.MagicMock()
        instance.country.places.not_merged.return_value.exclude.return_value.iterator.return_value = iter([])
        instance.country.places.not_merged.return_value.exclude.return_value.count.return_value = 0
        return instance

    def test_hooks_bracket_the_merge(self, importer):
        order = []
        importer.before_merge = lambda: order.append('before')
        importer.after_merge = lambda: order.append('after')
        importer._merge_places = lambda: order.append('merge')

        importer.do_merge()

        assert order == ['before', 'merge', 'after']


class TestPoland:
    def test_poland_uses_the_hook_rather_than_an_override(self):
        assert PolandImporter.do_merge is BaseImporter.do_merge
        assert PolandImporter.before_merge is not BaseImporter.before_merge

    def test_poland_no_longer_assembles_its_own_border(self):
        """
        It used to union every subarea whose name contained "województwo" or
        "powiat". The base takes the border from the extract's own
        admin_level=2 relation, which does not depend on Polish words.
        """
        assert PolandImporter.do_update_country_border is BaseImporter.do_update_country_border

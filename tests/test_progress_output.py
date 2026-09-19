"""
Import output stays readable at country scale.

The importer used to print one line per object. That is 5,222 lines for the
Isle of Man and 733,016 for Poland -- enough to bury the phase timings, and no
indication of how far along anything was. Progress bars replaced it.
"""
import inspect
import re

from django_osm_locations.management.commands.importers import base as importer_base
from django_osm_locations.management.commands.importers import poland
from django_osm_locations.search import indexer


def source(module):
    return inspect.getsource(module)


class TestNoPerObjectOutput:
    def test_importer_does_not_print_a_line_per_object(self):
        found = re.findall(
            r"print\(\s*'(?:CountrySubarea|Place|PlaceSubarea|Street):'", source(importer_base)
        )
        assert not found, 'one line per imported object floods the output'

    def test_merge_does_not_write_a_line_per_duplicate(self):
        for module in (importer_base, poland):
            assert 'merged with {}!' not in source(module), \
                'one line per merged row is another flood at country scale'

    def test_phase_timings_are_still_printed(self):
        """The bars replace the noise, not the phase summary."""
        text = source(importer_base)
        assert 'Importing Relations' in text
        assert 'Parsing ended' in text


class TestProgressBars:
    def test_parse_passes_report_progress(self):
        text = source(importer_base)
        for desc in ("desc='relations'", "desc='ways'", "desc='nodes'"):
            assert desc in text, desc

    def test_import_passes_report_progress_with_a_total(self):
        """A known total means a percentage and an ETA, not just a counter."""
        text = source(importer_base)
        assert 'total=len(self.relations)' in text
        assert 'total=len(self.ways)' in text
        assert 'total=len(self.nodes)' in text

    def test_reindex_reports_progress(self):
        text = source(indexer)
        assert 'progress(' in text
        assert "unit='doc'" in text

    def test_progress_helper_leaves_stdout_alone(self):
        """
        tqdm writes to stderr by default, which is what keeps piping the
        command's stdout somewhere useful working.
        """
        signature = inspect.signature(importer_base.progress)
        assert 'iterable' in signature.parameters
        assert 'total' in signature.parameters

    def test_progress_passes_values_through_unchanged(self):
        assert list(importer_base.progress([1, 2, 3], total=3)) == [1, 2, 3]

    def test_progress_handles_an_unknown_total(self):
        """Reading a .pbf has no count in the header, so there is no total."""
        assert list(importer_base.progress(iter('abc'))) == ['a', 'b', 'c']


class TestNoPython2Leftovers:
    """
    Two Python 2 idioms have already reached production here: itertools.count
    .next() in the cache backends, and generator .next() in the place-subarea
    merge. Both survived because no extract in the test set exercised them.
    """

    def test_no_dot_next_calls(self):
        import pathlib

        import django_osm_locations

        root = pathlib.Path(django_osm_locations.__file__).parent
        offenders = []
        for path in root.rglob('*.py'):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if '.next()' in line and not line.strip().startswith('#'):
                    offenders.append('{}:{}'.format(path.relative_to(root), number))
        assert not offenders, 'Python 2 iterator protocol: {}'.format(offenders)

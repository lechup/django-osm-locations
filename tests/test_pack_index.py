"""
The index of published packs: which release is a pack, what built it, what
loads it, and which pack of a country is the current one.
"""
import importlib.util
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope='module')
def index():
    path = os.path.join(HERE, '..', 'scripts', 'pack_index.py')
    spec = importlib.util.spec_from_file_location('pack_index', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V022 = '0e1d27c1234d4943195b25edc8cb4f89d29899fd'
VERSIONS = {V022: '0.2.2'}


def release(tag, target=V022, body='Needs django-osm-locations 0.2.0 or later; built with 0.2.2.',
            name=None, draft=False, parts=(100, 50)):
    assets = [{'name': '{}.tar.gz.part{:02d}'.format(tag, i), 'size': s} for i, s in enumerate(parts)]
    return {'tag_name': tag, 'target_commitish': target, 'body': body, 'draft': draft,
            'name': name or 'Poland · {}'.format(tag), 'html_url': 'https://x/' + tag,
            'assets': assets + [{'name': 'SHA256SUMS', 'size': 9}], 'published_at': '2026-11-17T00:00:00Z'}


class TestWhatIsAPack:
    def test_a_country_and_a_state_are_packs(self, index):
        assert index.parse_release(release('pl-2026-11-17'), VERSIONS)['token'] == 'pl'
        assert index.parse_release(release('us-ca-2026-10-24'), VERSIONS)['token'] == 'us-ca'

    def test_code_releases_and_the_index_are_not(self, index):
        assert index.parse_release(release('v0.2.3'), VERSIONS) is None
        assert index.parse_release(release('packs'), VERSIONS) is None

    def test_a_draft_is_not_published_yet(self, index):
        assert index.parse_release(release('pl-2026-11-17', draft=True), VERSIONS) is None


class TestWhatBuiltIt:
    def test_the_target_commit_names_the_version(self, index):
        """The tag's target is the commit of the code that made the pack: that beats prose."""
        row = index.parse_release(release('pl-2026-11-17', body='built with 9.9.9'), VERSIONS)
        assert row['built_with'] == '0.2.2'
        assert row['commit'] == V022

    def test_the_notes_when_the_target_is_a_branch(self, index):
        row = index.parse_release(release('pl-2026-11-17', target='master'), VERSIONS)
        assert row['built_with'] == '0.2.2'
        assert row['commit'] is None

    def test_what_loads_it_comes_from_the_notes(self, index):
        assert index.parse_release(release('pl-2026-11-17'), VERSIONS)['loads_with'] == '0.2.0'
        old = release('pl-2026-10-25', body='Needs django-osm-locations 0.2.0 or later (v0.2.0).')
        assert index.parse_release(old, VERSIONS)['loads_with'] == '0.2.0'

    def test_only_the_parts_are_counted_in_the_size(self, index):
        row = index.parse_release(release('pl-2026-11-17'), VERSIONS)
        assert (row['size'], row['parts']) == (150, 2)
        assert row['name'] == 'Poland'


class TestTheCurrentPack:
    def test_the_newest_of_a_country_is_current_and_the_rest_point_at_it(self, index):
        packs = index.build([release('pl-2026-10-25'), release('pl-2026-11-17'),
                             release('us-ca-2026-10-24')], VERSIONS)
        by = {p['pack']: p for p in packs}
        assert by['pl-2026-11-17']['current'] and by['us-ca-2026-10-24']['current']
        assert not by['pl-2026-10-25']['current']
        assert by['pl-2026-10-25']['replaced_by'] == 'pl-2026-11-17'

    def test_the_table_lists_current_packs_and_the_replaced_apart(self, index):
        packs = index.build([release('pl-2026-10-25'), release('pl-2026-11-17')], VERSIONS)
        text = index.markdown(packs, '2026-11-22 00:00 UTC')
        current, replaced = text.split('## Replaced')
        assert '[pl-2026-11-17](https://x/pl-2026-11-17) | 2026-11-17 | 0.2.2 | 0.2.0 or later' in current
        assert 'pl-2026-10-25' not in current
        assert '| pl-2026-11-17 |' in replaced

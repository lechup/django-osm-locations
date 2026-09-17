"""
The invariant that broke search: three implementations must agree.

* the stored ``slug`` column     -- django slugify(anyascii(name))
* ``search_vector*`` in Postgres -- the database slugify() function
* the incoming API query         -- django_osm_locations.utils.slugify

If any of these drifts, prefix search silently returns nothing instead of
failing loudly, so it is worth pinning all three.
"""
import re

import pytest
from django.utils.text import slugify as django_slugify
from anyascii import anyascii

from django_osm_locations.utils.slugify import slugify as query_slugify


def stored_slug(name):
    """What LocationBase.slugify writes to the slug column."""
    return django_slugify(anyascii(name))


# Names chosen for the transliterations that actually differ between schemes:
# German umlauts and eszett, Polish diacritics, Czech kroužek, ligatures.
NAMES = [
    'München', 'Münster', 'Lübeck', 'Köln', 'Osnabrück', 'Zürich',
    'Straße', 'Æbeltoft', 'Rœux', 'Þingvellir',
    'Kraków', 'Gdańsk', 'Świnoujście', 'Łódź', 'Nowy Sącz', 'Bielsko-Biała',
    'Dvůr Králové', 'Ústí nad Labem', 'Plzeň',
]


@pytest.mark.parametrize('name', NAMES)
def test_query_normalisation_matches_stored_slug(name):
    """
    Regression: the query side used a hand-maintained map that rendered
    German umlauts as "ue"/"oe" while the stored slug used "u"/"o", so
    "München" was stored as "munchen" but searched for as "muenchen".
    """
    assert query_slugify(name.lower()) == stored_slug(name)


@pytest.mark.parametrize('name,expected', [
    ('München', 'munchen'),
    ('Straße', 'strasse'),
    ('Kraków', 'krakow'),
    ('Dvůr Králové', 'dvur-kralove'),
    ('Bielsko-Biała', 'bielsko-biala'),
])
def test_known_slugs(name, expected):
    assert stored_slug(name) == expected
    assert query_slugify(name) == expected


@pytest.mark.parametrize('char,expected', [
    ('ù', 'u'), ('ú', 'u'), ('û', 'u'), ('ū', 'u'),
    ('ů', 'u'), ('ű', 'u'), ('ŭ', 'u'), ('ũ', 'u'), ('ų', 'u'),
])
def test_u_diacritics_are_not_deleted(char, expected):
    """These nine mapped to the empty string in the old character map."""
    assert query_slugify(char) == expected


@pytest.mark.parametrize('char,expected', [('ô', 'o'), ('û', 'u'), ('ø', 'o')])
def test_circumflex_pair_is_not_transposed(char, expected):
    """The old SQL map had ô->u and û->o swapped, and ø->d."""
    assert query_slugify(char) == expected


def test_overwrite_char_map_does_not_leak_between_calls():
    """It used to mutate a module-level dict, corrupting every later call."""
    query_slugify('x', overwrite_char_map={'ą': 'XX'})
    assert query_slugify('Gdąnsk') == 'gdansk'


def test_slugify_without_django_slugify_keeps_case():
    assert query_slugify('Kraków', do_slugify=False) == 'Krakow'


def test_sql_function_is_generated_not_handwritten():
    """
    Guard the generation invariant: the migration must carry the full
    transliteration table, not a short hand-maintained one. The original had
    79 entries and disagreed with it on 119 characters.
    """
    import pathlib

    import django_osm_locations

    migration = (
        pathlib.Path(django_osm_locations.__file__).parent
        / 'migrations' / '0030_align_slugify_with_anyascii.py'
    ).read_text(encoding='utf-8')

    translate = re.search(
        r"translate\(\s*str,\s*'((?:[^']|'')*)',\s*'((?:[^']|'')*)'\s*\)",
        migration, re.S,
    )
    assert translate, 'translate() pair not found in migration 0030'
    src, tgt = (g.replace("''", "'") for g in translate.groups())
    assert len(src) == len(tgt), 'translate() arguments must be the same length'
    assert len(src) > 150, f'expected the full transliteration table, got {len(src)} entries'

    # every single-char mapping must be what anyascii produces
    mismatched = [(a, b) for a, b in zip(src, tgt) if anyascii(a) != b]
    assert not mismatched, f'SQL disagrees with anyascii: {mismatched[:10]}'

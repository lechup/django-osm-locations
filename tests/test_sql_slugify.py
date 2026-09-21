"""
The database slugify() function, exercised against a real PostGIS instance.

test_slugify.py asserts the generated table matches anyascii; this asserts
Postgres actually evaluates it the same way.
"""
import pytest
from django.db import connection

from django_osm_locations.models import Place
from django_osm_locations.utils.slugify import slugify as python_slugify

pytestmark = pytest.mark.django_db


def sql_slugify(value):
    with connection.cursor() as cursor:
        cursor.execute('SELECT slugify(%s)', [value])
        return cursor.fetchone()[0]


@pytest.mark.parametrize('name', [
    'München', 'Münster', 'Lübeck', 'Köln', 'Straße', 'Zürich',
    'Kraków', 'Gdańsk', 'Świnoujście', 'Łódź', 'Nowy Sącz', 'Bielsko-Biała',
    'Dvůr Králové', 'Ústí nad Labem', 'Æbeltoft', 'Rœux',
    # a slash separates words rather than joining them
    'Derry/Londonderry', 'Nad Kanałem k/Krynki', 'Alba / Scotland',
    'Dział/Dzioły', 'A/B/C',
])
def test_sql_slugify_matches_python(name):
    """This is the bug that made umlaut places unsearchable."""
    assert sql_slugify(name) == python_slugify(name)


@pytest.mark.parametrize('name,expected', [
    ('Derry/Londonderry', 'derry-londonderry'),
    ('Nad Kanałem k/Krynki', 'nad-kanalem-k-krynki'),
    ('Dział/Dzioły', 'dzial-dzioly'),
])
def test_a_slash_separates_rather_than_fusing(name, expected):
    """
    It is not slug-safe, so it used to be dropped and the words on either
    side ran together: derrylondonderry, nad-kanalem-kkrynki.
    """
    assert sql_slugify(name) == expected
    assert python_slugify(name) == expected


@pytest.mark.parametrize('name', [
    'Derry/Londonderry', 'München', 'Nad Kanałem k/Krynki',
])
def test_the_model_agrees_with_both(name):
    """
    Three implementations have to produce the same string. When they
    disagree nothing raises -- the stored slug and the search vector come
    from one, the query is normalised by another, and the place merely
    becomes unfindable.
    """
    assert Place(name=name).slugify == sql_slugify(name) == python_slugify(name)


def test_sql_slugify_collapses_separators():
    assert sql_slugify('  Nowy   Sącz  ') == 'nowy-sacz'


def test_sql_slugify_strips_punctuation():
    assert sql_slugify('Kraków!!!') == 'krakow'

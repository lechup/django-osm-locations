"""
The database slugify() function, exercised against a real PostGIS instance.

test_slugify.py asserts the generated table matches unidecode; this asserts
Postgres actually evaluates it the same way.
"""
import pytest
from django.db import connection
from django.utils.text import slugify as django_slugify
from unidecode import unidecode

pytestmark = pytest.mark.django_db


def sql_slugify(value):
    with connection.cursor() as cursor:
        cursor.execute('SELECT slugify(%s)', [value])
        return cursor.fetchone()[0]


@pytest.mark.parametrize('name', [
    'München', 'Münster', 'Lübeck', 'Köln', 'Straße', 'Zürich',
    'Kraków', 'Gdańsk', 'Świnoujście', 'Łódź', 'Nowy Sącz', 'Bielsko-Biała',
    'Dvůr Králové', 'Ústí nad Labem', 'Æbeltoft', 'Rœux',
])
def test_sql_slugify_matches_python(name):
    """This is the bug that made umlaut places unsearchable."""
    assert sql_slugify(name) == django_slugify(unidecode(name))


def test_sql_slugify_collapses_separators():
    assert sql_slugify('  Nowy   Sącz  ') == 'nowy-sacz'


def test_sql_slugify_strips_punctuation():
    assert sql_slugify('Kraków!!!') == 'krakow'

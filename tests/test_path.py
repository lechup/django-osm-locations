"""
A readable path for each result.

ancestry keeps every level separately, as filtering needs -- a city with county
rights is a county, a commune and a place, three entities all called Żory.
Printed as they are that reads "Polska › śląskie › Żory › Żory › Żory". The
path collapses the repeats; ancestry itself is left alone.
"""
from django_osm_locations.api.views import add_path


def hit(name, *ancestry):
    return {'name': name, 'ancestry': [
        {'level': level, 'slug': slug, 'name': label} for level, slug, label in ancestry]}


ZORY = hit(
    'Łąkowa 59',
    ('country', 'polska', 'Polska'),
    ('countrysubarea', 'wojewodztwo-slaskie', 'województwo śląskie'),
    ('countrysubarea', 'zory', 'Żory'),
    ('countrysubarea', 'zory-gmina', 'Żory'),
    ('place', 'zory-place', 'Żory'),
    ('placesubarea', 'rogozna', 'Rogoźna'),
)


def test_repeated_names_collapse():
    assert add_path(dict(ZORY))['path'] == [
        'Polska', 'województwo śląskie', 'Żory', 'Rogoźna', 'Łąkowa 59']


def test_ancestry_is_left_alone():
    """Filtering by within= needs every level's slug, repeats included."""
    result = add_path(dict(ZORY))
    assert [e['slug'] for e in result['ancestry']] == [
        'polska', 'wojewodztwo-slaskie', 'zory', 'zory-gmina', 'zory-place', 'rogozna']


def test_a_place_named_like_its_commune_is_not_printed_twice():
    place = hit('Żory', ('country', 'polska', 'Polska'), ('countrysubarea', 'zory', 'Żory'))
    assert add_path(place)['path'] == ['Polska', 'Żory']


def test_only_neighbours_collapse():
    """The same name further apart is two places and stays twice."""
    street = hit('Warszawska', ('country', 'polska', 'Polska'),
                 ('place', 'warszawa', 'Warszawa'), ('placesubarea', 'wola', 'Wola'))
    assert add_path(street)['path'] == ['Polska', 'Warszawa', 'Wola', 'Warszawska']


def test_empty_names_are_skipped():
    assert add_path(hit('Kowale', ('country', 'polska', ''), ('place', 'x', 'Kowale')))['path'] == [
        'Kowale']


def test_no_ancestry_is_just_the_name():
    assert add_path({'name': 'Isle of Man'})['path'] == ['Isle of Man']


class TestNoNumericId:
    """Each country is built in its own database; row ids repeat across them."""

    def test_a_result_carries_no_id(self):
        from django_osm_locations.api.views import format_hit

        hit = {'_source': {'id': 17, 'slug': 'douglas', 'name': 'Douglas'}, '_score': 1.0}
        result = format_hit(hit)
        assert 'id' not in result
        assert result['slug'] == 'douglas' and result['score'] == 1.0

    def test_the_hit_itself_is_not_changed(self):
        from django_osm_locations.api.views import format_hit

        source = {'id': 17, 'slug': 'douglas'}
        format_hit({'_source': source})
        assert source['id'] == 17

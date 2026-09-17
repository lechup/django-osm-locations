"""API behaviour that needs PostGIS."""
import pytest

from .conftest import results

pytestmark = pytest.mark.django_db


def test_place_list(client, place):
    response = client.get('/api/places/', HTTP_HOST='example.com')
    assert response.status_code == 200
    assert len(results(response)) == 1


def test_list_omits_heavy_geometry(client, place):
    row = results(client.get('/api/places/', HTTP_HOST='example.com'))[0]
    assert 'border' not in row
    assert row['slug'] == 'krakow'


def test_detail_includes_geometry(client, place):
    response = client.get('/api/places/krakow/', HTTP_HOST='example.com')
    assert response.status_code == 200
    assert response.json()['border'] is not None


def test_lookup_is_by_slug(client, place):
    assert client.get('/api/places/krakow/', HTTP_HOST='example.com').status_code == 200
    assert client.get('/api/places/nope/', HTTP_HOST='example.com').status_code == 404


@pytest.mark.parametrize('query', ['krak', 'krakow', 'Krak'])
def test_prefix_search_by_name(client, place, query):
    from django.core.management import call_command
    call_command('locations_recalculate_denormalized_fields')

    response = client.get(
        '/api/places/', {'search_vector_name': query}, HTTP_HOST='example.com',
    )
    assert response.status_code == 200
    assert len(results(response)) == 1


@pytest.mark.parametrize('query', ['münchen', 'munchen', 'münch'])
def test_prefix_search_finds_umlaut_places(client, place_with_umlaut, query):
    """
    Regression: the query was normalised to "muenchen" while the index held
    "munchen", so no German place could ever be found by name.
    """
    from django.core.management import call_command
    call_command('locations_recalculate_denormalized_fields')

    response = client.get(
        '/api/places/', {'search_vector_name': query}, HTTP_HOST='example.com',
    )
    assert response.status_code == 200
    assert len(results(response)) == 1, f'{query!r} found nothing'

"""
Host parsing for PlaceSubdomainMiddleware.

These run without a database: get_subdomain_from_host is a pure function of the
Host header and settings.ROOT_URL.
"""
import pytest
from django.test import RequestFactory, override_settings

from django_osm_locations.middleware import PlaceSubdomainMiddleware

parse = PlaceSubdomainMiddleware.get_subdomain_from_host


def request_for(host):
    return RequestFactory().get('/', HTTP_HOST=host)


@override_settings(ROOT_URL='example.com')
@pytest.mark.parametrize('host,expected', [
    # a place subdomain
    ('krakow.example.com', 'krakow'),
    ('krakow.example.com:8000', 'krakow'),
    ('KRAKOW.EXAMPLE.COM', 'krakow'),
    ('bielsko-biala.example.com', 'bielsko-biala'),
    # the apex domain is not a place
    ('example.com', None),
    ('example.com:8000', None),
    # reserved subdomains are not places -- "www" used to 404 as a place slug
    ('www.example.com', None),
    ('api.example.com', None),
    # not our domain at all
    ('evil.com', False),
    ('notexample.com', False),
    ('example.com.evil.com', False),
    # deeper nesting is not a place
    ('a.b.example.com', False),
])
def test_host_parsing(host, expected):
    assert parse(request_for(host)) == expected


@override_settings(ROOT_URL='example.co.uk')
@pytest.mark.parametrize('host,expected', [
    ('example.co.uk', None),
    ('krakow.example.co.uk', 'krakow'),
    ('www.example.co.uk', None),
])
def test_multipart_tld(host, expected):
    """Dot counting read 'example.co.uk' as the subdomain 'example'."""
    assert parse(request_for(host)) == expected


@override_settings(ROOT_URL='localhost:8000')
@pytest.mark.parametrize('host,expected', [
    ('localhost:8000', None),
    ('krakow.localhost:8000', 'krakow'),
])
def test_local_development(host, expected):
    """Single-label hosts were redirected away, breaking local development."""
    assert parse(request_for(host)) == expected


@override_settings(ROOT_URL='example.com', LOCATIONS_RESERVED_SUBDOMAINS={'shop'})
@pytest.mark.parametrize('host,expected', [
    ('shop.example.com', None),
    ('www.example.com', 'www'),   # no longer reserved once overridden
])
def test_reserved_subdomains_are_configurable(host, expected):
    assert parse(request_for(host)) == expected


@override_settings(ROOT_URL='example.com')
def test_place_is_always_set_on_apex():
    """
    Regression: request.place only existed on the subdomain branch, so any view
    reading it on the apex domain raised AttributeError.
    """
    captured = {}

    def view(request):
        captured['place'] = request.place
        captured['subdomain'] = request.subdomain
        from django.http import HttpResponse
        return HttpResponse('ok')

    middleware = PlaceSubdomainMiddleware(view)
    response = middleware(request_for('example.com'))

    assert response.status_code == 200
    assert captured['place'] is None
    assert captured['subdomain'] is None


@override_settings(ROOT_URL='example.com')
def test_foreign_host_redirects_to_root():
    middleware = PlaceSubdomainMiddleware(lambda r: None)
    response = middleware(request_for('evil.com'))
    assert response.status_code == 301
    assert response['Location'] == 'http://example.com'

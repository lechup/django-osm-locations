"""
Looking a location up by slug, once several countries are loaded.

A slug is unique within a country, not across the world -- that is what lets
each country's index be built and published on its own. The cost lands here:
with several countries under one alias, a slug can name more than one place.
Across Poland, Britain and Denmark that is 60 slugs, mostly cycle trails
called "jurassic-park" and short Danish-English villages like "dale".

Taking the first hit was safe while slugs were globally unique. It is not any
more: the winner would depend on the order shards come back in, which is not a
decision anyone made.
"""
import pytest
from rest_framework.test import APIRequestFactory

from django_osm_locations.api.views import DetailView


def hit(slug, name, ancestry):
    return {'_source': {'slug': slug, 'name': name, 'ancestry_slugs': ancestry,
                        'ancestry': [], 'ancestry_names': [], 'type': 'streets'},
            '_score': 1.0}


class FakeClient:
    """Returns whatever hits it is given, and records the query it was asked."""

    def __init__(self, hits):
        self._hits = hits
        self.bodies = []

    def search(self, index, body, **kwargs):
        self.bodies.append(body)
        return {'hits': {'hits': self._hits}}


@pytest.fixture
def view(monkeypatch):
    def run(hits, query=''):
        client = FakeClient(hits)
        monkeypatch.setattr('django_osm_locations.api.views.get_client', lambda: client)
        request = APIRequestFactory().get('/api/streets/dale/' + query)
        response = DetailView().get(
            DetailView().initialize_request(request), 'streets', 'dale')
        return response, client
    return run


class TestOneMatch:
    def test_it_is_returned(self, view):
        response, _ = view([hit('dale', 'Dale', ['polska'])])
        assert response.status_code == 200
        assert response.data['name'] == 'Dale'


class TestNoMatch:
    def test_it_is_a_404(self, view):
        response, _ = view([])
        assert response.status_code == 404


class TestSeveralMatches:
    def test_it_refuses_to_pick_one(self, view):
        """
        The winner would otherwise be whichever shard answered first.
        """
        response, _ = view([
            hit('dale', 'Dale', ['polska']),
            hit('dale', 'Dale', ['danmark']),
        ])
        assert response.status_code == 409

    def test_it_names_the_candidates(self, view):
        response, _ = view([
            hit('dale', 'Dale', ['polska']),
            hit('dale', 'Dale', ['danmark']),
        ])
        ancestries = [c['ancestry_slugs'] for c in response.data['candidates']]
        assert ['polska'] in ancestries and ['danmark'] in ancestries

    def test_it_says_how_to_resolve_it(self, view):
        response, _ = view([
            hit('dale', 'Dale', ['polska']),
            hit('dale', 'Dale', ['danmark']),
        ])
        assert 'within' in response.data['detail']

    def test_it_does_not_ask_for_only_one_hit(self, view):
        """
        size=1 is what made the ambiguity invisible: a second match could not
        be seen, so it could not be reported.
        """
        _, client = view([hit('dale', 'Dale', ['polska'])])
        assert client.bodies[0]['size'] > 1


class TestNarrowingByAncestor:
    def test_within_is_applied_as_a_filter(self, view):
        _, client = view([hit('dale', 'Dale', ['polska'])], query='?within=polska')
        filters = client.bodies[0]['query']['bool']['filter']
        assert {'term': {'ancestry_slugs': 'polska'}} in filters

    def test_the_slug_is_still_matched(self, view):
        _, client = view([hit('dale', 'Dale', ['polska'])], query='?within=polska')
        filters = client.bodies[0]['query']['bool']['filter']
        assert {'term': {'slug': 'dale'}} in filters

    def test_without_it_only_the_slug_is_matched(self, view):
        _, client = view([hit('dale', 'Dale', ['polska'])])
        assert client.bodies[0]['query']['bool']['filter'] == [{'term': {'slug': 'dale'}}]

    def test_it_resolves_what_was_ambiguous(self, view):
        """Narrowed to one country, there is one answer again."""
        response, _ = view([hit('dale', 'Dale', ['polska'])], query='?within=polska')
        assert response.status_code == 200


class TestUnknownType:
    def test_it_is_a_404_naming_the_known_types(self, monkeypatch):
        request = APIRequestFactory().get('/api/nonsense/x/')
        response = DetailView().get(
            DetailView().initialize_request(request), 'nonsense', 'x')
        assert response.status_code == 404
        assert 'streets' in response.data['detail']

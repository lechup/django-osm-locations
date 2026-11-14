"""
The slug repair asks for the place nearest each of a group's houses in
requests of at most NEAREST_BATCH searches, and keeps the answers in the
houses' order: one request for the 1,941 houses of boras-kommun-1 came back
with more headers than Python accepts.
"""
from django_osm_locations.management.commands import locations_repair_slugs
from django_osm_locations.management.commands.locations_repair_slugs import Command


class Client:
    def __init__(self):
        self.sizes = []

    def msearch(self, body):
        searches = body[1::2]
        self.sizes.append(len(searches))
        return {'responses': [
            {'hits': {'hits': [{'_source': {'name': 'near {}'.format(s['sort'][0]['_geo_distance']['center']['lon'])}}]}}
            for s in searches]}


def hits(count):
    return [{'_source': {'center': {'lon': n, 'lat': 0}, 'ancestry': []}} for n in range(count)]


def test_asks_in_batches_and_keeps_the_order():
    command = Command()
    command.client, command.places = Client(), 'places'
    count = 2 * locations_repair_slugs.NEAREST_BATCH + 41
    assert command.nearest_places(hits(count)) == ['near {}'.format(n) for n in range(count)]
    assert command.client.sizes == [locations_repair_slugs.NEAREST_BATCH] * 2 + [41]


def test_a_place_with_no_slug_is_no_place():
    command = Command()
    command.client, command.places = Client(), 'places'
    house = {'_source': {'center': {'lon': 1, 'lat': 0},
                         'ancestry': [{'level': 'country', 'name': 'United States', 'slug': 'united-states'},
                                      {'level': 'place', 'name': None, 'slug': None}]}}
    seen = []
    command.client.msearch = lambda body: seen.extend(body) or {'responses': [{'hits': {'hits': []}}]}
    assert command.nearest_places([house]) == [None]
    place_term = seen[1]['query']['bool']['filter'][0]['nested']['query']['bool']['filter'][1]
    assert place_term == {'term': {'ancestry.slug': ''}}


def test_no_houses_no_request():
    command = Command()
    command.client, command.places = Client(), 'places'
    assert command.nearest_places([]) == []
    assert command.client.sizes == []

"""
Giving an index built before whole words the fields that hold them.

The index is made the way the packs were: the mapping without the subfields,
and names_all one string of every translation.

Needs the OpenSearch container from docker-compose.test.yml:

    docker compose -f docker-compose.test.yml up -d
    pytest -m search
"""
import copy
import uuid

import pytest
from django.core.management import call_command

from django_osm_locations.search.indexes import INDEXES, SETTINGS

pytestmark = [pytest.mark.search]

pytest.importorskip('opensearchpy', reason='needs opensearch-py')

WARSZAWA = {'name': 'Warszawa', 'type': 'places', 'slug': 'warszawa',
            'names': {'en': 'Warsaw', 'de': 'Warschau', 'fr': 'Varsovie'},
            'names_all': 'Varsovie Warsaw Warschau',
            'ancestry_names': ['Polska', 'województwo mazowieckie']}


def as_the_packs_were(properties):
    """The mapping before the WORDS subfields, and names_all.exact."""
    properties = copy.deepcopy(properties)
    properties['name']['fields'].pop('words')
    properties['names_all'].pop('fields')
    properties['ancestry_names'].pop('fields')
    return properties


@pytest.fixture
def places(settings):
    from django_osm_locations.search.client import get_client

    client = get_client()
    try:
        client.info()
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip('OpenSearch is not reachable: {}'.format(exc))
    prefix = 'test-words-{}'.format(uuid.uuid4().hex[:8])
    index = '{}-places-pl-1'.format(prefix)
    client.indices.create(index=index, body={
        'settings': SETTINGS,
        'mappings': {'properties': as_the_packs_were(INDEXES['places'].properties)}})
    client.indices.put_alias(index=index, name='{}-places'.format(prefix))
    client.index(index=index, id='1', body=WARSZAWA, refresh=True)
    client.index(index=index, id='2', refresh=True, body={
        'name': 'powiat warszawski zachodni', 'type': 'places', 'slug': 'powiat-warszawski-zachodni',
        'names': None, 'names_all': None, 'ancestry_names': ['Polska']})
    settings.LOCATIONS_OPENSEARCH_INDEX_PREFIX = prefix
    yield client, index
    client.indices.delete(index='{}-*'.format(prefix), ignore_unavailable=True)


def ids(client, index, query):
    return sorted(h['_id'] for h in client.search(index=index, body={'query': query})['hits']['hits'])


class TestTheRepair:
    def test_the_mapping_gains_the_subfields(self, places):
        client, index = places
        call_command('locations_repair_search_fields')
        mapping = client.indices.get_mapping(index=index)[index]['mappings']['properties']
        assert set(mapping['name']['fields']) == {'exact', 'keyword', 'words'}
        assert set(mapping['names_all']['fields']) == {'exact', 'words'}
        assert set(mapping['ancestry_names']['fields']) == {'words'}

    def test_one_value_per_translation(self, places):
        client, index = places
        call_command('locations_repair_search_fields')
        source = client.get(index=index, id='1')['_source']
        assert source['names_all'] == ['Varsovie', 'Warsaw', 'Warschau']

    def test_none_stays_none(self, places):
        client, index = places
        call_command('locations_repair_search_fields')
        assert client.get(index=index, id='2')['_source']['names_all'] is None

    def test_a_whole_translation_is_found(self, places):
        client, index = places
        assert ids(client, index, {'match': {'names_all.exact': 'warsaw'}}) == []
        call_command('locations_repair_search_fields')
        assert ids(client, index, {'match': {'names_all.exact': 'warsaw'}}) == ['1']

    def test_a_whole_word_and_not_a_prefix(self, places):
        client, index = places
        call_command('locations_repair_search_fields')
        assert ids(client, index, {'match': {'name.words': 'warszawa'}}) == ['1']
        assert ids(client, index, {'match': {'name.words': 'warszawski'}}) == ['2']
        assert ids(client, index, {'match': {'ancestry_names.words': 'mazowieckie'}}) == ['1']

    def test_twice_writes_nothing_more(self, places, capsys):
        call_command('locations_repair_search_fields')
        capsys.readouterr()
        call_command('locations_repair_search_fields')
        assert 'places: 0 to write again' in capsys.readouterr().out

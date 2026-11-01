"""
A geometry OpenSearch refuses costs the document its geometry, not the run.

Mecklenburg-Vorpommern's coastal waters, clipped to land, were 9,651 crumbs,
one of them a triangle OpenSearch could not orient. It failed its bulk
request, and with it Germany's reindex, three days into the build.
"""
from unittest import mock

import pytest
from django.contrib.gis.geos import MultiPolygon

from django_osm_locations.models import Place
from django_osm_locations.search import indexer as indexer_module
from django_osm_locations.search.indexer import Reindexer
from django_osm_locations.search.indexes import INDEXES

from .conftest import square

pytest.importorskip('opensearchpy', reason='needs opensearch-py')
from opensearchpy.helpers import BulkIndexError  # noqa: E402

pytestmark = pytest.mark.django_db

ORIENTATION = 'Cannot determine orientation: edges adjacent to (12.6994498,54.4018187) coincide'


def refusal(action, kind, reason, caused_by=None):
    error = {'type': kind, 'reason': reason}
    if caused_by:
        error['caused_by'] = {'type': 'invalid_shape_exception', 'reason': caused_by}
    return {'index': {'_index': action['_index'], '_id': str(action['_id']), 'status': 400,
                      'error': error}}


class FakeIndices:
    def refresh(self, index):
        pass


class FakeClient:
    """What populate talks to: bulk through the helper, then single documents."""

    def __init__(self):
        self.indices = FakeIndices()
        self.indexed = {}

    def index(self, index, id, body):
        self.indexed[id] = body


def fake_bulk(client, actions, chunk_size, raise_on_error):
    """OpenSearch's answers: the sliver's geometry refused, a bad name refused, the rest kept."""
    assert raise_on_error is False, 'a refusal has to come back, not be raised'
    ok, errors = 0, []
    for action in actions:
        name = action['_source']['name']
        if name == 'Sliver':
            errors.append(refusal(action, 'mapper_parsing_exception',
                                  'failed to parse field [geometry] of type [geo_shape]', ORIENTATION))
        elif name == 'Broken':
            errors.append(refusal(action, 'mapper_parsing_exception',
                                  'failed to parse field [population] of type [long]'))
        else:
            client.indexed[action['_id']] = action['_source']
            ok += 1
    return ok, errors


@pytest.fixture
def places():
    return [Place.objects.create(name=name, border=MultiPolygon(square(x, x)))
            for x, name in enumerate(['Kraków', 'Sliver', 'Gdańsk'], 1)]


def populate():
    client = FakeClient()
    reindexer = Reindexer(INDEXES['places'], client=client, prefix='test',
                          suffix='1', show_progress=False)
    with mock.patch.object(indexer_module, '_load_bulk', return_value=fake_bulk):
        success, _ = reindexer.populate()
    return reindexer, client, success


class TestARefusedGeometry:
    def test_the_document_goes_in_without_it(self, places):
        _, client, _ = populate()
        sliver = places[1]
        assert client.indexed[sliver.id]['geometry'] is None
        assert client.indexed[sliver.id]['name'] == 'Sliver'

    def test_the_rest_keep_theirs(self, places):
        _, client, _ = populate()
        assert client.indexed[places[0].id]['geometry']['type'] == 'MultiPolygon'
        assert client.indexed[places[2].id]['geometry']['type'] == 'MultiPolygon'

    def test_every_row_is_counted(self, places):
        """The count against the database decides the promote, and nothing is short."""
        reindexer, _, success = populate()
        assert success == reindexer.indexed == 3

    def test_it_is_listed_with_opensearchs_reason(self, places):
        reindexer, _, _ = populate()
        assert reindexer.without_geometry == [(places[1].id, 'Sliver', ORIENTATION)]


class TestAnyOtherRefusal:
    def test_still_fails_the_run(self, places):
        Place.objects.create(name='Broken', border=MultiPolygon(square(5, 5)))
        with pytest.raises(BulkIndexError):
            populate()

    def test_only_a_geometry_field_counts(self):
        action = {'_index': 'x', '_id': 1}
        assert indexer_module._geometry_refused(refusal(
            action, 'mapper_parsing_exception', 'failed to parse field [geometry] of type [geo_shape]'))
        assert not indexer_module._geometry_refused(refusal(
            action, 'mapper_parsing_exception', 'failed to parse field [population] of type [long]'))
        assert not indexer_module._geometry_refused(refusal(
            action, 'version_conflict_engine_exception', '[geometry] conflict'))

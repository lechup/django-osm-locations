"""
Merging an index before it is snapshotted, against a real OpenSearch: a
document written again just before leaves nothing deleted behind.

OpenSearch deletes softly, and a merge keeps what a shard's retention lease
still covers; the lease catches up with the last write only about every
30 s, so this takes that long.

Needs the OpenSearch container from docker-compose.test.yml:

    docker compose -f docker-compose.test.yml up -d
    pytest -m search
"""
import uuid

import pytest

from django_osm_locations.search import snapshots

pytestmark = pytest.mark.search

pytest.importorskip('opensearchpy', reason='needs opensearch-py')


@pytest.fixture
def opensearch():
    from django_osm_locations.search.client import get_client

    client = get_client()
    try:
        client.info()
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip('OpenSearch is not reachable: {}'.format(exc))
    return client


@pytest.fixture
def written_again(opensearch):
    """An index of three documents, one of them written twice, in a segment each."""
    index = 'test-merge-{}'.format(uuid.uuid4().hex[:8])
    opensearch.indices.create(index=index, body={'settings': {'number_of_replicas': 0}})
    for pk in (1, 2, 3, 1):
        opensearch.index(index=index, id=pk, body={'name': 'n{}'.format(pk)},
                         params={'refresh': 'true'})
    yield index
    opensearch.indices.delete(index=index)


def test_a_document_written_again_leaves_nothing_deleted(opensearch, written_again):
    before = opensearch.indices.stats(index=written_again, metric='docs')
    assert before['_all']['primaries']['docs']['deleted'] == 1

    snapshots.merge([written_again], client=opensearch, interval=1)

    after = opensearch.indices.stats(index=written_again, metric='docs')
    assert after['_all']['primaries']['docs'] == {'count': 3, 'deleted': 0}
    segments = opensearch.indices.segments(index=written_again)
    shard, = segments['indices'][written_again]['shards']['0']
    assert len([s for s in shard['segments'].values() if s['search']]) == 1

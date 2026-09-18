"""Connection to OpenSearch, configured from Django settings."""
from django.conf import settings


def _load_opensearch():
    try:
        from opensearchpy import OpenSearch
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            "Searching needs opensearch-py. "
            "Install it with: pip install 'django-osm-locations[search]'"
        ) from exc
    return OpenSearch


def get_hosts():
    """
    Where OpenSearch lives.

    ``LOCATIONS_OPENSEARCH_HOSTS`` is a list of host dicts or URLs, e.g.
    ``['http://localhost:9200']``.
    """
    hosts = getattr(settings, 'LOCATIONS_OPENSEARCH_HOSTS', None)
    return list(hosts) if hosts else ['http://localhost:9200']


def get_index_prefix():
    """
    Prefix for every index and alias this app owns.

    Lets several environments share a cluster without colliding.
    """
    return str(getattr(settings, 'LOCATIONS_OPENSEARCH_INDEX_PREFIX', 'locations'))


def get_client(**overrides):
    """
    Build an OpenSearch client.

    Extra connection arguments come from ``LOCATIONS_OPENSEARCH_CLIENT_KWARGS``
    -- credentials, TLS settings, timeouts.
    """
    OpenSearch = _load_opensearch()
    kwargs = {
        'hosts': get_hosts(),
        'timeout': 30,
        'retry_on_timeout': True,
        'max_retries': 3,
    }
    kwargs.update(getattr(settings, 'LOCATIONS_OPENSEARCH_CLIENT_KWARGS', {}) or {})
    kwargs.update(overrides)
    return OpenSearch(**kwargs)

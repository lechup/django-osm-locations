"""
Building indexes from PostGIS.

The rebuild is deliberately non-destructive on both sides: it never touches the
database, and it never deletes the index that is currently serving traffic. A
rebuild writes a brand new index, compares its document count against the
database, and only then moves the alias across.
"""
from datetime import datetime, timezone

from django.conf import settings

from ..utils.progress import progress
from .client import get_client, get_index_prefix
from .indexes import INDEXES


def _load_bulk():
    try:
        from opensearchpy.helpers import bulk
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            "Searching needs opensearch-py. "
            "Install it with: pip install 'django-osm-locations[search]'"
        ) from exc
    return bulk


def new_index_suffix():
    return datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')


class Reindexer:
    """
    Rebuild one location type's index.

    Usage is three steps so a caller can inspect between them:

        reindexer.create()     # new empty index
        reindexer.populate()   # stream rows from PostGIS
        reindexer.promote()    # point the alias at it
    """

    def __init__(self, index, client=None, prefix=None, suffix=None, batch_size=500,
                 show_progress=None):
        self.index = index
        self.client = client or get_client()
        self.prefix = prefix or get_index_prefix()
        self.suffix = suffix or new_index_suffix()
        self.batch_size = batch_size
        self.indexed = 0
        # None leaves it to terminal detection, matching the importer
        if show_progress is None:
            show_progress = getattr(settings, 'LOCATIONS_SHOW_PROGRESS', None)
        self.show_progress = show_progress

    @property
    def alias(self):
        return self.index.alias(self.prefix)

    @property
    def index_name(self):
        return self.index.index_name(self.prefix, self.suffix)

    def database_count(self):
        return self.index.queryset().count()

    def indexed_count(self, name=None):
        """Documents in an index, or 0 when it does not exist."""
        name = name or self.index_name
        if not self.client.indices.exists(index=name):
            return 0
        self.client.indices.refresh(index=name)
        return int(self.client.count(index=name)['count'])

    def live_count(self):
        """Documents currently served through the alias."""
        if not self.client.indices.exists_alias(name=self.alias):
            return 0
        self.client.indices.refresh(index=self.alias)
        return int(self.client.count(index=self.alias)['count'])

    def create(self):
        self.client.indices.create(index=self.index_name, body=self.index.body())
        return self.index_name

    def _actions(self, total=None):
        """
        Documents to index, with a progress bar.

        Indexing a country is hundreds of thousands of documents and several
        minutes; the bar goes to stderr so piping stdout still works.
        """
        rows = progress(
            self.index.queryset().iterator(chunk_size=self.batch_size),
            total=total,
            desc=self.index.name,
            unit='doc',
            show=self.show_progress,
        )
        for obj in rows:
            yield {
                '_index': self.index_name,
                '_id': obj.id,
                '_source': self.index.document(obj),
            }

    def populate(self, total=None):
        bulk = _load_bulk()
        success, errors = bulk(
            self.client,
            self._actions(total=total),
            chunk_size=self.batch_size,
            raise_on_error=True,
        )
        self.indexed = success
        self.client.indices.refresh(index=self.index_name)
        return success, errors

    def promote(self):
        """
        Move the alias onto the new index, atomically.

        The previously aliased index is left in place: it is the rollback path,
        and removing it is a separate, explicit decision.
        """
        actions = [{'add': {'index': self.index_name, 'alias': self.alias}}]
        replaced = self.aliased_indexes()
        for old in replaced:
            if old != self.index_name:
                actions.append({'remove': {'index': old, 'alias': self.alias}})
        self.client.indices.update_aliases(body={'actions': actions})
        return replaced

    def aliased_indexes(self):
        """Indexes the alias currently points at."""
        if not self.client.indices.exists_alias(name=self.alias):
            return []
        return sorted(self.client.indices.get_alias(name=self.alias))

    def existing_indexes(self):
        """Every index this type has ever built, oldest first."""
        pattern = self.index.index_name(self.prefix, '*')
        if not self.client.indices.exists(index=pattern):
            return []
        return sorted(self.client.indices.get(index=pattern))


def reindexers(names=None, **kwargs):
    """Reindexers for the named location types, or all of them."""
    selected = names or list(INDEXES)
    unknown = [name for name in selected if name not in INDEXES]
    if unknown:
        raise ValueError(
            'Unknown location type(s): {}. Known: {}'.format(
                ', '.join(unknown), ', '.join(sorted(INDEXES))
            )
        )
    return [Reindexer(INDEXES[name], **kwargs) for name in selected]

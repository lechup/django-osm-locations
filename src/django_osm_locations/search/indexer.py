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
                 show_progress=None, country=None):
        self.index = index
        self.client = client or get_client()
        self.prefix = prefix or get_index_prefix()
        self.suffix = suffix or new_index_suffix()
        #: A Country row when this rebuild covers one country, None for all of
        #: them. Decides which rows are indexed, what the index is called, and
        #: -- the part that matters most -- which other indexes the alias is
        #: taken away from.
        self.country = country
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
        return self.index.index_name(self.prefix, self.suffix, self.country_token)

    @property
    def country_token(self):
        """The country's ISO 3166-1 alpha-2, lowercased, or None."""
        return self.country.iso2.lower() if self.country else None

    def queryset(self):
        """
        Rows to index, narrowed to one country when this is a country build.

        Country itself carries no denormalised country array -- it is what the
        arrays point at -- so it is matched on its own primary key.
        """
        queryset = self.index.queryset()
        if not self.country:
            return queryset
        if 'country' in (getattr(self.index.model, 'denorm_fields', None) or ()):
            return queryset.filter(
                country__contains=[[self.country.name, self.country.slug]]
            )
        return queryset.filter(pk=self.country.pk)

    def database_count(self):
        return self.queryset().count()

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
            self.queryset().iterator(chunk_size=self.batch_size),
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

    def replaces(self, index_name):
        """
        Whether this rebuild supersedes an index already under the alias.

        A country rebuild replaces only that country. Rebuilding Poland must
        leave Denmark under the alias, or merging countries would mean each
        import silently evicting the last -- the alias is the whole mechanism
        by which several countries are searched as one.

        A rebuild covering everything replaces everything, country indexes
        included.
        """
        if index_name == self.index_name:
            return False
        stem = '{}-{}-'.format(self.prefix, self.index.name)
        if not index_name.startswith(stem):
            return False
        if not self.country:
            return True
        return index_name[len(stem):].startswith('{}-'.format(self.country_token))

    def promote(self):
        """
        Move the alias onto the new index, atomically.

        The previously aliased index is left in place: it is the rollback path,
        and removing it is a separate, explicit decision.
        """
        actions = [{'add': {'index': self.index_name, 'alias': self.alias}}]
        replaced = [old for old in self.aliased_indexes() if self.replaces(old)]
        for old in replaced:
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

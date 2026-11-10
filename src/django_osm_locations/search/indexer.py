"""
Building indexes from PostGIS.

The rebuild is deliberately non-destructive on both sides: it never touches the
database, and it never deletes the index that is currently serving traffic. A
rebuild writes a brand new index, compares its document count against the
database, and only then moves the alias across.
"""
import itertools
from datetime import datetime, timezone

from django.conf import settings
from django.db import connection, transaction
from django.db.models.expressions import RawSQL

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


def _bulk_item(error):
    """A bulk error's item: ``{'index': {...}}`` unwrapped."""
    return next(iter(error.values()))


def _geometry_refused(error):
    """Whether a bulk error is OpenSearch refusing the document's geometry."""
    item = _bulk_item(error)
    detail = item.get('error') or {}
    return (isinstance(detail, dict) and detail.get('type') == 'mapper_parsing_exception'
            and '[geometry]' in detail.get('reason', ''))


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
                 show_progress=None, country=None, token=None):
        self.index = index
        self.client = client or get_client()
        self.prefix = prefix or get_index_prefix()
        self.suffix = suffix or new_index_suffix()
        #: A Country row when this rebuild covers one country, None for all of
        #: them. Decides which rows are indexed, what the index is called, and
        #: -- the part that matters most -- which other indexes the alias is
        #: taken away from.
        self.country = country
        #: What names this rebuild's indexes and decides what it replaces, when
        #: not the country's code: one state of the United States, ``us-ca``,
        #: replaces that state and leaves the other 52 under the alias.
        self.token = token
        self.batch_size = batch_size
        self.indexed = 0
        #: ``(id, name, reason)`` of the documents indexed without the
        #: geometry OpenSearch refused.
        self.without_geometry = []
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
        """
        The country's ISO 3166-1 alpha-2, lowercased, or None -- or the token
        it was given, ``us-ca``, for a country built a part at a time.
        """
        if getattr(self, 'token', None):
            return self.token
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
            # The expression migration 0042 indexes, written out the same way
            # so the planner recognises it. country__contains asked the same
            # question of an unindexed array: every country's addresses were
            # found by reading the whole 12GB table, and a country imported
            # last waited for the scan to reach its rows before the first
            # document went out.
            column = '"{}"."country"'.format(self.index.model._meta.db_table)
            # and unordered: a document's id is its row's, so the order is
            # nobody's concern, and sorting Germany's 20 million addresses
            # spilled 12GB to disk before the first one went out
            return queryset.annotate(_country_slug=RawSQL(
                "coalesce(nullif({0}[1][2], ''), {0}[2][2])".format(column), (),
            )).filter(_country_slug=self.country.slug).order_by()
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
        rows = iter(progress(
            self.queryset().iterator(chunk_size=self.batch_size),
            total=total,
            desc=self.index.name,
            unit='doc',
            show=self.show_progress,
        ))
        # a batch at a time, so the OSM ids of what was merged into each row
        # are one query per batch rather than one per row
        for batch in iter(lambda: list(itertools.islice(rows, self.batch_size)), []):
            merged = self.index.merged_osm_ids([obj.id for obj in batch])
            twins = self.index.twin_fields([obj.id for obj in batch])
            for obj in batch:
                yield {
                    '_index': self.index_name,
                    '_id': obj.id,
                    '_source': self.index.document(obj, merged.get(obj.id), twins.get(obj.id)),
                }

    def populate(self, total=None):
        """
        Stream the queryset into the index.

        Inside a transaction, because outside one Django declares the
        server-side cursor WITH HOLD, and Postgres answers that by writing the
        whole result to a temporary file before handing over the first row: 25
        minutes of silence before Poland's 6.9 million addresses, 20 before
        Denmark's 2.6 million. Without the hold, rows arrive as they are read.

        A cursor is planned for its first tenth of rows, and for a fast start
        the planner walks the primary key and tests every row's country:
        Germany's streets came out of all 11 million at 25 a second, where
        the country index and a sort fetch them all in minutes. Every row is
        read, so the cursor is planned for all of them.

        A geometry OpenSearch refuses costs that document its geometry, not
        the run. Mecklenburg-Vorpommern's coastal waters, clipped to land,
        were 9,651 crumbs along the coast, one of them a triangle too thin for
        OpenSearch to tell which way round it goes: "Cannot determine
        orientation". It failed the bulk request it was in, and with it
        Germany's reindex, three days into the country's build. Such a
        document goes in again without its geometry and is listed in
        ``without_geometry``; any other refusal still fails the run.
        """
        bulk = _load_bulk()
        with transaction.atomic():
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute('SET LOCAL cursor_tuple_fraction = 1.0')
            success, errors = bulk(
                self.client,
                self._actions(total=total),
                chunk_size=self.batch_size,
                raise_on_error=False,
            )
        refused = [error for error in errors if _geometry_refused(error)]
        others = [error for error in errors if not _geometry_refused(error)]
        if others:
            from opensearchpy.helpers import BulkIndexError

            raise BulkIndexError(
                '{} document(s) failed to index.'.format(len(others)), others)
        success += self._index_without_geometry(refused)
        self.indexed = success
        self.client.indices.refresh(index=self.index_name)
        return success, errors

    def _index_without_geometry(self, refused):
        """Index again, with no geometry, the documents whose geometry was refused."""
        if not refused:
            return 0
        reasons = {}
        for error in refused:
            item = _bulk_item(error)
            cause = item['error'].get('caused_by') or item['error']
            reasons[int(item['_id'])] = cause.get('reason', '')
        indexed = []
        merged = self.index.merged_osm_ids(list(reasons))
        twins = self.index.twin_fields(list(reasons))
        for obj in self.queryset().filter(pk__in=list(reasons)):
            document = self.index.document(obj, merged.get(obj.id), twins.get(obj.id))
            document['geometry'] = None
            self.client.index(index=self.index_name, id=obj.id, body=document)
            indexed.append((obj.id, obj.name, reasons[obj.id]))
        self.without_geometry.extend(sorted(indexed))
        return len(indexed)

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

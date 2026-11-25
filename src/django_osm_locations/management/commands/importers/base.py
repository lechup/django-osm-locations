import re
import os
import tempfile
from array import array
from contextlib import contextmanager
from datetime import datetime

from django.conf import settings
from django.contrib.gis.geos import MultiLineString, LineString, Polygon, Point, MultiPolygon
from django.contrib.gis.geos.error import GEOSException
from django.contrib.postgres.aggregates.general import ArrayAgg
from django.db import connection, transaction
from django.contrib.gis.db.models.aggregates import Union
from django.db.models import Count, Min
from tqdm import tqdm

from .. import Address, Country, CountrySubarea, Place, PlaceSubarea, Street, \
    MergeMultiline, array_agg_ordered
from ....conf import get_cache_dir
from ....utils.progress import progress, show_progress_default  # noqa: F401
from ....utils.settlements import streets_of


# pyosmium and shapely are only needed to parse a .pbf extract. Import them
# lazily so that merely importing this module -- which Django does whenever it
# enumerates management commands -- does not hard-fail without the extras.
def _load_osmium():
    try:
        import osmium
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            "Parsing OSM extracts needs pyosmium. "
            "Install it with: pip install 'django-osm-locations[importers]'"
        ) from exc
    return osmium


def _load_polygonize_full():
    try:
        from shapely.ops import polygonize_full
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            "Building borders from OSM ways needs Shapely. "
            "Install it with: pip install 'django-osm-locations[importers]'"
        ) from exc
    return polygonize_full


def _location_storage():
    """
    Which libosmium index backs the node-location lookup.

    libosmium resolves way geometry itself, which is what replaced the
    hand-rolled node cache. ``flex_mem`` keeps the index in RAM and is fine up
    to country size; the ``*_file_array`` variants spill to disk for larger
    extracts. Override with ``LOCATIONS_LOCATION_STORAGE``, or set
    ``LOCATIONS_DISK_CACHE`` for a disk-backed default.
    """
    configured = getattr(settings, 'LOCATIONS_LOCATION_STORAGE', None)
    if configured:
        return str(configured)
    if getattr(settings, 'LOCATIONS_DISK_CACHE', False):
        return 'sparse_file_array'
    return 'flex_mem'


@contextmanager
def _location_index():
    """
    The node-location index, with somewhere for a file-backed one to live.

    A ``*_file_array`` index is only file-backed when libosmium is given a
    file: the bare type name is accepted and maps anonymous memory instead,
    so it is as resident as ``flex_mem`` while reading as though it were not.
    Asking for ``sparse_file_array`` on a 4.8GB France extract still grew by
    twelve gigabytes in three minutes, which is enough for systemd-oomd to
    take the desktop session down -- with the file named, the same pass holds
    about 200MB of private memory and the rest is page cache the kernel can
    drop.

    The file goes next to the other caches and is removed afterwards. It is
    the size of the extract's node table -- roughly 16 bytes a node, so tens
    of gigabytes for a large country -- and nothing reads it once the pass
    that wrote it has finished.
    """
    spec = _location_storage()
    if ',' in spec or not spec.endswith('_file_array'):
        # an explicit path, or an index that was never going to touch disk
        yield spec
        return

    directory = get_cache_dir()
    os.makedirs(directory, exist_ok=True)
    handle, path = tempfile.mkstemp(
        prefix='osm-node-locations-', suffix='.idx', dir=directory)
    os.close(handle)
    try:
        yield '{},{}'.format(spec, path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass



class CoordinateView:
    """
    A read-only ``(lon, lat)`` sequence over a flat ``array('d')``.

    Way coordinates are stored flat to keep a country-sized import in memory,
    but everything that consumes them -- the tag predicates, LineString, the
    closed-way check -- expects pairs. This presents the flat buffer as pairs
    without copying it.
    """

    __slots__ = ('_flat',)

    def __init__(self, flat):
        self._flat = flat

    def __len__(self):
        return len(self._flat) // 2

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return self._flat[index * 2], self._flat[index * 2 + 1]

    def __iter__(self):
        flat = self._flat
        for i in range(0, len(flat), 2):
            yield flat[i], flat[i + 1]

    def __bool__(self):
        return len(self._flat) > 0


class BatchUpserter:
    """
    Accumulate ``update_or_create``-shaped writes and apply them in batches.

    One ``update_or_create`` per object costs a SELECT and an INSERT/UPDATE
    round trip each, which is what made writing a country's streets the slowest
    part of an import by a wide margin. This buffers them, resolves a whole
    batch's existing rows in a single query, and applies the rest with
    ``bulk_create`` / ``bulk_update``.

    ``bulk_create`` and ``bulk_update`` bypass ``Model.save()`` and therefore
    the ``_presave_*`` hooks, so the hooks are invoked explicitly here. Their
    names also say which fields they derive -- ``_presave_bbox`` produces
    ``bbox`` -- so the update field list stays correct without a second list to
    maintain.
    """

    #: Rows created before the table's statistics are first refreshed; after
    #: that, each time the count doubles.
    ANALYZE_FROM = 10000

    def __init__(self, model, lookup_field, batch_size=1000):
        self.model = model
        self.lookup_field = lookup_field
        self.batch_size = batch_size
        self._pending = {}
        self.created = 0
        self.updated = 0
        self._analyzed_at = 0

    @property
    def derived_fields(self):
        prefix = '_presave_'
        return {
            name[len(prefix):]
            for name in self.model._save_hooks()
            if name.startswith(prefix)
        }

    def add(self, lookup_value, defaults):
        # last write wins within a batch, matching repeated update_or_create
        self._pending[lookup_value] = defaults
        if len(self._pending) >= self.batch_size:
            self.flush()

    def flush(self):
        if not self._pending:
            return

        lookup = self.lookup_field
        pending, self._pending = self._pending, {}

        existing = {
            getattr(obj, lookup): obj
            for obj in self.model.objects.filter(**{'%s__in' % lookup: list(pending)})
        }

        to_create, to_update, changed_fields = [], [], set()
        for lookup_value, defaults in pending.items():
            obj = existing.get(lookup_value)
            if obj is None:
                obj = self.model(**{lookup: lookup_value}, **defaults)
                obj._presave()
                to_create.append(obj)
            else:
                for field, value in defaults.items():
                    setattr(obj, field, value)
                obj._presave()
                to_update.append(obj)
                changed_fields.update(defaults)

        with transaction.atomic():
            if to_create:
                self.model.objects.bulk_create(to_create, batch_size=self.batch_size)
                self.created += len(to_create)
            if to_update:
                fields = sorted(changed_fields | self.derived_fields)
                self.model.objects.bulk_update(to_update, fields, batch_size=self.batch_size)
                self.updated += len(to_update)
        self._refresh_statistics()

    def _refresh_statistics(self):
        """
        Tell the planner how big the table has grown.

        A country's own database starts from a template whose tables were
        never analysed, and the whole import is one transaction, so autovacuum
        never sees a row of it. Without statistics a lookup of 1,000 ids is
        estimated at a million rows and planned as a sequential scan: every
        batch read the whole table, and the Netherlands' addresses slowed to
        940,000 an hour by the eighth million. ANALYZE inside the transaction
        counts the rows it inserted, so the lookups go back to the index.
        """
        if self.created < max(self.ANALYZE_FROM, 2 * self._analyzed_at):
            return
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('ANALYZE "{}"'.format(self.model._meta.db_table))
        self._analyzed_at = self.created


#: ``name:<suffix>`` keys that are not languages at all.
#:
#: OSM overloads the namespace with grammar and provenance, and this is not a
#: corner case: ``name:genitive`` is the single most common ``name:*`` tag in
#: the Poland extract at 49,118 occurrences, ahead of every real language.
#: Taking everything after the colon would index Polish noun declensions as
#: though they were locales.
NON_LANGUAGE_NAME_KEYS = frozenset({
    'genitive', 'prefix', 'suffix', 'adjective', 'abbreviation',
    'etymology', 'start_date', 'end_date', 'botanical', 'source',
    'wikipedia', 'wikidata', 'left', 'right', 'signed', 'pronunciation',
})

#: A language tag: two or three letters, optionally with a script or region
#: subtag -- pl, csb, zh-Hant, pt-BR. Anything else is a convention we have
#: not seen and would rather skip than mistake for a locale.
LANGUAGE_TAG = re.compile(r'^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$')


def language_names(tags):
    """
    Translations of ``name``, keyed by language.

    Skips the grammatical and provenance suffixes above, and anything that
    does not look like a language tag.
    """
    names = {}
    for key in tags:
        if not key.startswith('name:'):
            continue
        suffix = key[len('name:'):]
        if suffix in NON_LANGUAGE_NAME_KEYS or not LANGUAGE_TAG.match(suffix):
            continue
        value = tags.get(key)
        if value:
            names[suffix] = value
    return names


class BaseImporter(object):
    #: How this country identifies itself. Declaring it here rather than in a
    #: table somewhere else is what makes adding a country a one-file job: the
    #: registry finds the class, the download path and the Country row both
    #: come from these, and nothing else has to be edited.
    #:
    #: ``geofabrik_name`` is the stem Geofabrik uses in the download URL, and
    #: doubles as the name typed on the command line.
    geofabrik_name = None
    geofabrik_path = 'europe/'
    #: The name typed on the command line, when it is not the extract's: a
    #: country inside another's extract, as San Marino is inside Italy's.
    name = None
    #: (min lon, min lat, max lon, max lat) to read of the extract, for such a
    #: country: the corner it sits in is cut out first, and only that is read.
    extract_bbox = None
    #: The name that imports a whole set of these, when this is part of a
    #: country imported a piece at a time: ``united-states`` for each state.
    group = None
    #: For such a part: the whole country's extract, path and name, that the
    #: borders a part's own extract cuts off are taken from. Only the
    #: relations ``is_border_relation`` accepts are read of it.
    borders_from = None
    #: Neighbours' extracts, path and name, a member way of those borders is
    #: looked for in when the whole country's extract lacks it too.
    borders_fill_from = ()

    @classmethod
    def index_token_for(cls):
        """
        What tells this one's indexes and pack apart from the rest: the
        country's ISO code, or a state's ``us-ca`` when a country comes in
        parts that each have to be replaced on their own.
        """
        return cls.iso2.lower()
    #: Further extracts holding parts of the country, as ``path/name`` --
    #: ``'africa/canary-islands'`` for Spain. They are merged with the main
    #: one into a single file before the import reads it.
    extra_extracts = ()
    country_name = None
    country_slug = None
    iso2 = None
    iso3 = None
    un = None

    whitelist_tags = ['name', 'admin_level', 'boundary', 'place', 'highway', 'population',
                      'ISO3166-1', 'ISO3166-1:alpha2',
                      # An address states which street and settlement it is on,
                      # which 0.6% of roads do for themselves.
                      'addr:housenumber', 'addr:street', 'addr:city', 'addr:place',
                      'addr:postcode']
    location_models_mergable = [CountrySubarea, Place, PlaceSubarea, Street]
    location_models_not_mergable = []
    location_models = location_models_mergable + location_models_not_mergable

    def __init__(self, country_slug, file_path, verbose=0, show_progress=None):
        self.country = Country.objects.get(slug=country_slug)

        self.file_path = file_path
        self.verbose = verbose
        # None leaves it to terminal detection; a command with -v 0 passes False
        self.show_progress = show_progress

        # Relations and ways we keep are a small subset of the extract, so plain
        # dicts are fine. Node coordinates are *not* kept here at all: libosmium
        # resolves way geometry during the read, which is what removed the need
        # for the packed coordinate store this importer used to carry.
        self.relations = {}
        self.ways = {}
        self.nodes = {}

        # ids referenced by relations we kept, so pass two knows what to retain
        self.needed_way_ids = set()
        self.needed_node_ids = set()

        # An extract is a rectangle, not a country: Denmark's reaches into
        # Germany and Sweden, Norway's into Finland and Russia. The country's
        # own admin_level=2 relation is in there too, so it is used to reject
        # everything that is not actually in the country.
        self.country_boundary_members = None
        self.country_boundary = None      # prepared, for membership tests
        self.country_border = None        # the geometry itself

        # Writes are buffered: one update_or_create per object dominated import
        # time. Keyed by the OSM id field each import path looks up on, and
        # created when that path first writes rather than declared here. The
        # declaration used to be a fixed list, and the address pass arrived with
        # two lookup fields of its own that nobody added to it -- a KeyError 52
        # minutes into a real import, after every other pass had succeeded.
        self.write_batch_size = int(
            getattr(settings, 'LOCATIONS_WRITE_BATCH_SIZE', 1000) or 1000)
        self.writers = {}

        # place can be imported from relation - it's center is node that later is imported also as new place
        # we use that cache to cache nodes_ids of relation centers, so that we won't have duplicates
        # see: https://www.openstreetmap.org/relation/3267973
        self.already_imported_places_node_ids = set()

    # ------------------------------------------------------------------ hooks
    #
    # Country importers differ in more than which admin_level means what. Poland
    # has to collapse a city and the county sharing its name; the UK's four
    # constituent countries are level-4 subdivisions that arguably belong
    # somewhere else entirely. Rather than each such country overriding
    # do_import or do_merge wholesale and remembering to call super(), they hook
    # in at a named point.
    #
    # All four are no-ops. Override what you need; there is no need to chain.

    def before_import(self):
        """
        Run after the extract is parsed, before anything is written.

        Everything parsed is available -- self.relations, self.ways, self.nodes
        and the country boundary -- so this is where to inspect or adjust what
        is about to be imported.
        """

    def after_import(self):
        """
        Run once every relation, way and node has been written.

        The rows exist and the country border is set, so this is where to
        reshape them: promote something, split something, fix a country's own
        idea of itself.
        """

    def before_merge(self):
        """Run at the start of do_merge, before any duplicate is resolved."""

    def after_merge(self):
        """Run once merging has finished."""

    def do_import(self):
        self._parse_and_cache()

        # save cache to db
        start_all = datetime.now()
        print("Import started %s.\n" % start_all)

        self.before_import()

        start = datetime.now()
        print("Importing Relations %s ..." % start)
        with transaction.atomic():
            self._import_relations()
            self._flush_writes()
        end = datetime.now()
        print("Importing Relations ended: %s, took: %s!\n" % (end, end - start))

        start = datetime.now()
        print("Importing Ways %s ..." % start)
        with transaction.atomic():
            self._import_ways()
            self._flush_writes()
        end = datetime.now()
        print("Importing Ways ended: %s, took: %s!\n" % (end, end - start))

        start = datetime.now()
        print("Importing Coords and Nodes %s ..." % start)
        with transaction.atomic():
            self._import_nodes()
            self._flush_writes()
        end = datetime.now()
        print("Importing Coords and Nodes ended: %s, took: %s!\n" % (end, end - start))

        self.do_update_country_border()

        # After the border, because each address is filtered against it as it
        # arrives, and this pass keeps nothing to filter afterwards.
        with transaction.atomic():
            self.import_addresses()

        self.after_import()

        end = datetime.now()
        print("Importing ended: %s, took: %s." % (end, end - start_all))

    def do_merge(self):
        """
        Use this method in child class to merge locations (eg. multiple entries of same street or city subarea)

        Country-specific work belongs in before_merge or after_merge rather than
        in an override of this method.
        """
        self.before_merge()

        # remove duplicates from places
        self._merge_places()

        # remove duplicates from locations below places eg:
        # streets (one street can be saved as more than one line in OSM)
        places = self.country.places.not_merged().exclude(border=None).iterator()
        places_count = self.country.places.not_merged().exclude(border=None).count()
        for place in progress(places, total=places_count,
                desc='Doing merge of Places...', leave=False):
            self._merge_place_subareas(place)
            self._merge_place_streets(place)

        self.after_merge()

    def do_update_country_border(self):
        """
        Rebuild the border of the country this importer was constructed for.

        The extract's own admin_level=2 relation is used when it was found,
        which is both more accurate and safe in a database holding several
        countries. Unioning the subareas, the older approach, took every
        CountrySubarea in the database regardless of which country it belonged
        to -- with Poland and Denmark both imported that gave Denmark a border
        stretching to the Bug.

        Instance method so that ``do_import`` can call it as
        ``self.do_update_country_border()`` and country importers can override
        it (see ``PolandImporter``).
        """
        if self.country_border is not None:
            self.country.border = self._merge_multipolygon(self.country_border)
            self.country.save(update_fields=['border', 'bbox', 'center', 'area'])
            print("Border of %s taken from the extract.\n" % self.country)
            return
        self._rebuild_country_border(self.country)

    @classmethod
    def update_country_border(cls, slug):
        """
        Rebuild a country border without instantiating an importer -- used by
        the ``locations_clear_not_in_country`` management command.
        """
        cls._rebuild_country_border(Country.objects.get(slug=slug))

    @classmethod
    def _rebuild_country_border(cls, country):
        """
        Union every country subarea into the country border.

        Done by PostGIS rather than in Python: accumulating
        ``border += subarea.border`` pulled every subarea geometry into memory
        and rebuilt a progressively larger MultiPolygon on each iteration, which
        is quadratic in both time and allocation over thousands of subareas.

        NOTE: this merges *every* CountrySubarea in the database, not only the
        ones belonging to ``country``. That is the long-standing behaviour and
        is correct for a single-country database; importers for a second
        country should override ``do_update_country_border`` (as Poland does)
        until the subareas carry a country foreign key.
        """
        print("Update'ing {} border....".format(country))
        border = CountrySubarea.objects.exclude(border=None).aggregate(
            border=Union('border'),
        )['border']
        if border is None:
            return
        if isinstance(border, Polygon):
            border = MultiPolygon(border)
        country.border = cls._merge_multipolygon(border)
        # Save the derived fields too. With update_fields=['border'] the bbox,
        # center and area that _presave recomputes were discarded, leaving
        # country.bbox empty -- and since country.places filters on
        # bbox_polygon, the whole merge phase silently matched nothing.
        country.save(update_fields=['border', 'bbox', 'center', 'area'])

    def _progress(self, iterable=None, total=None, desc=None, unit='obj'):
        return progress(iterable, total=total, desc=desc, unit=unit,
                        show=self.show_progress)

    def _upsert(self, model, lookup_field, lookup_value, defaults):
        key = (model, lookup_field)
        writer = self.writers.get(key)
        if writer is None:
            writer = self.writers[key] = BatchUpserter(
                model, lookup_field, self.write_batch_size)
        writer.add(lookup_value, defaults)

    def _flush_writes(self):
        for writer in self.writers.values():
            writer.flush()

    def _parse_and_cache(self):
        """
        Read the extract and keep only what the import needs.

        Two passes, not four. libosmium applies the tag filters in C++ and
        resolves node locations itself, so only the handful of objects this
        importer actually cares about ever cross into Python -- on a country
        extract that is thousands of objects rather than hundreds of millions.

        Pass one collects the relations we keep and notes which ways and nodes
        they refer to. Pass two reads ways and nodes with locations attached,
        keeping those the relations need plus those that are locations in their
        own right (named highways, place nodes).
        """
        start_all = datetime.now()
        print("Parsing for %s started %s.\n" % (self.country, start_all))

        self._parse_relations()
        self._parse_ways()
        self._build_country_boundary()
        self._parse_nodes()

        end = datetime.now()
        print("Parsing ended: %s, took: %s!\n" % (end, end - start_all))

    @classmethod
    def _wanted_tag(cls, key):
        """
        Whether a tag is worth carrying through the parse.

        ``name:<lang>`` is wanted but cannot be whitelisted by name -- there
        are 380 such keys in the Poland extract alone. The language test runs
        here rather than at import time so that ``name:genitive`` and its
        kind, which are grammar rather than locales and outnumber every real
        language, are never cached at all.
        """
        if key in cls.whitelist_tags:
            return True
        if key.startswith('name:'):
            suffix = key[len('name:'):]
            return suffix not in NON_LANGUAGE_NAME_KEYS and bool(LANGUAGE_TAG.match(suffix))
        return False

    @classmethod
    def _tags_to_dict(cls, obj):
        """OSM tags as a plain dict, reduced to what the import needs."""
        return {tag.k: tag.v for tag in obj.tags if cls._wanted_tag(tag.k)}

    def _parse_relations(self):
        start = datetime.now()
        print("Reading Relations %s ..." % start)
        osmium = _load_osmium()

        processor = (
            osmium.FileProcessor(self.file_path, osmium.osm.RELATION)
            .with_filter(osmium.filter.EmptyTagFilter())
        )
        for relation in self._progress(processor, desc='relations', unit='rel'):
            members = [
                (member.ref, member.type, member.role)
                for member in relation.members
            ]
            tags = self._tags_to_dict(relation)
            if self._is_country_boundary(tags):
                self.country_boundary_members = members
                for ref, kind, _role in members:
                    if kind == 'w':
                        self.needed_way_ids.add(ref)
            if not self._is_relation_to_cache(relation.id, tags, members):
                continue
            self.relations[relation.id] = (tags, members)
            for ref, kind, _role in members:
                if kind == 'w':
                    self.needed_way_ids.add(ref)
                elif kind == 'n':
                    self.needed_node_ids.add(ref)

        end = datetime.now()
        print("Relations read: %s, took: %s! (%d kept)\n" % (
            end, end - start, len(self.relations)))

    def _parse_ways(self):
        """
        Read ways with their geometry already resolved.

        Nodes must be in the entity mask for libosmium to build the location
        index, but ``EntityFilter`` drops them again in C++ so only ways cross
        into Python. That distinction is the whole point: letting every node
        reach Python is slower than the four-pass approach it replaced.
        """
        start = datetime.now()
        print("Reading Ways %s ..." % start)
        osmium = _load_osmium()

        with _location_index() as storage:
            processor = (
                osmium.FileProcessor(self.file_path, osmium.osm.WAY | osmium.osm.NODE)
                .with_locations(storage)
                .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
            )
            for way in self._progress(processor, desc='ways', unit='way'):
                self._keep_way(way)

        end = datetime.now()
        print("Ways read: %s, took: %s! (%d kept)\n" % (end, end - start, len(self.ways)))

    def _parse_nodes(self):
        """
        Read the nodes that can become places, then any relation centres missed.

        Tagged nodes are selected in C++ by ``EmptyTagFilter``. Relation centre
        nodes are usually tagged and therefore already caught; the few that are
        not are fetched by id in a second, cheap filtered read.
        """
        start = datetime.now()
        print("Reading Nodes %s ..." % start)
        osmium = _load_osmium()

        processor = (
            osmium.FileProcessor(self.file_path, osmium.osm.NODE)
            .with_filter(osmium.filter.EmptyTagFilter())
        )
        for node in self._progress(processor, desc='nodes', unit='node'):
            self._keep_node(node)

        missing = [
            osm_id for osm_id in self.needed_node_ids if osm_id not in self.nodes
        ]
        if missing:
            by_id = (
                osmium.FileProcessor(self.file_path, osmium.osm.NODE)
                .with_filter(osmium.filter.IdFilter(missing))
            )
            for node in self._progress(by_id, total=len(missing), desc='centre nodes', unit='node'):
                self._keep_node(node, force=True)

        end = datetime.now()
        print("Nodes read: %s, took: %s! (%d kept, %d fetched by id)\n" % (
            end, end - start, len(self.nodes), len(missing)))

    # tag keys that can make an untagged-by-relation way interesting on its own
    way_keys_of_interest = ('highway', 'place')

    @staticmethod
    def _admin_level(tags):
        """
        The admin_level tag as an integer, or None.

        Kept on the row because it is what distinguishes a constituent country
        from a county from a gmina once several countries share a database.
        """
        try:
            return int(tags['admin_level'])
        except (KeyError, TypeError, ValueError):
            return None

    def _is_country_boundary(self, tags):
        """
        Whether this relation is the border of the country being imported.

        Matched on the ISO code rather than the name, because the name in the
        extract is the local one and can carry several languages at once --
        "Suomi / Finland", "België / Belgique / Belgien".
        """
        if tags.get('admin_level') != '2':
            return False
        code = tags.get('ISO3166-1') or tags.get('ISO3166-1:alpha2')
        return bool(code) and code.upper() == (self.country.iso2 or '').upper()

    def _build_country_boundary(self):
        """
        Turn the captured relation into a polygon to test membership against.

        Prepared, because this is asked once per imported object and a plain
        contains() over a country outline with tens of thousands of points is
        far too slow for that.
        """
        if not self.country_boundary_members:
            print("No admin_level=2 relation for %s in this extract; "
                  "importing everything it contains.\n" % self.country)
            return
        border = self._get_border_from_members(self.country_boundary_members)
        if border is None:
            print("Could not assemble a border for %s; "
                  "importing everything this extract contains.\n" % self.country)
            return
        self.country_border = border
        self.country_boundary = border.prepared
        print("Country boundary assembled for %s.\n" % self.country)
        open_ends = self._open_ends(self.country_boundary_members)
        if open_ends:
            # Alaska's said "assembled" with its mainland's ring open for want
            # of one way, and imported as a few Aleutian islands
            print("WARNING: the border of %s does not close: %d open ends, the first at %s. "
                  "What lies inside the open ring is left out; the extract lacks a way of it.\n"
                  % (self.country, len(open_ends), open_ends[0]))

    def _open_ends(self, members):
        """Where a relation's outer ways stop without meeting another; none, for a closed border."""
        from collections import Counter

        ends = Counter()
        for line in self._get_multiline_from_members(
                [m for m in members if m[1] == 'w' and m[2] != 'inner']) or ():
            ends[line.coords[0]] += 1
            ends[line.coords[-1]] += 1
        return sorted(point for point, count in ends.items() if count % 2)

    def _within_country(self, geometry):
        """
        Whether a location belongs to the country being imported.

        Anything is accepted when no boundary could be established, so a missing
        or broken level-2 relation degrades to the old behaviour rather than
        importing nothing at all.
        """
        if self.country_boundary is None or geometry is None:
            return True
        try:
            point = geometry if geometry.geom_type == 'Point' else geometry.point_on_surface
        except Exception:
            return True
        return self.country_boundary.contains(point)

    def _keep_way(self, way):
        needed = way.id in self.needed_way_ids
        if not needed:
            # Probe the C++ tag list before building a Python dict or touching
            # node locations: most ways in an extract are neither a street nor a
            # place outline, and materialising them all dominated this pass.
            if not any(key in way.tags for key in self.way_keys_of_interest):
                return
            if 'name' not in way.tags:
                return
        tags = self._tags_to_dict(way)
        # Coordinates arrive resolved from libosmium. A way with a node outside
        # the extract is incomplete and cannot produce geometry, which is what
        # the old _are_all_refs_cached check was for.
        #
        # Stored flat in an array('d') rather than as a list of float tuples:
        # a country extract holds millions of these, and the tuple form costs
        # about 167 bytes per coordinate pair against 16 here.
        coords = array('d')
        for node in way.nodes:
            location = node.location
            if not location.valid():
                return
            coords.append(location.lon)
            coords.append(location.lat)
        if not needed and not self._is_way_to_cache(way.id, tags, CoordinateView(coords)):
            return
        self.ways[way.id] = (tags, coords)

    def _keep_node(self, node, force=False):
        tags = self._tags_to_dict(node)
        coords = (node.location.lon, node.location.lat)
        if force or node.id in self.needed_node_ids or self._is_node_to_cache(node.id, tags, coords):
            self.nodes[node.id] = (tags, coords)

    @classmethod
    def _tag_filter(cls, tags):
        # reduce memory usage
        for key in list(tags):
            if not cls._wanted_tag(key):
                del tags[key]

    def _are_all_members_cached(self, members):
        """
            Check if all @members are in country osm data vel in cache
            :param members: list of members eg. [(123, 'w', 'outer'), (234, 'n', 'inner') ...]
            :rtype : bool
        """
        for ref, kind, _role in members:
            if kind == 'n' and ref not in self.nodes:
                return False
            if kind == 'w' and ref not in self.ways:
                return False
        return True

    @classmethod
    def merge_multiline(cls, multilinestring):
        """
        Always return MultiLineString, even if merge result is LineString.
        """
        if multilinestring:
            multilinestring = multilinestring.merged
            if type(multilinestring) == LineString:
                multilinestring = MultiLineString(multilinestring)
            return multilinestring
        else:
            return None

    def _get_border_from_members(self, members):
        """
        A relation's border, with its holes.

        Every member way used to go into one polygonize, outer and inner
        alike. A ring with a hole comes out of that as two faces -- the ring,
        and the hole as a polygon of its own -- and the union of the two fills
        the hole back in: Niederösterreich covered Vienna, Bezirk Graz-Umgebung
        covered Graz, Minsk raion covered Minsk, and every street of Vienna
        was in Lower Austria. At the top, Italy's border covered San Marino
        and the Vatican, Switzerland's Büsingen and Campione.

        So the roles are read: the outer ways make the border, the inner ones
        what is cut out of it. A member with no role counts as outer, as OSM
        reads it.
        """
        outer = [m for m in members if m[1] == 'w' and m[2] != 'inner']
        inner = [m for m in members if m[1] == 'w' and m[2] == 'inner']
        border = self._get_multipolygon_from_multiline(self._get_multiline_from_members(outer))
        if border is None or not inner:
            return border
        holes = self._get_multipolygon_from_multiline(self._get_multiline_from_members(inner))
        if holes is None:
            return border
        cut = border.difference(holes)
        if cut.empty:
            return border
        if cut.geom_type == 'Polygon':
            cut = MultiPolygon(cut)
        elif cut.geom_type != 'MultiPolygon':
            # a difference can come back as a collection; keep its polygons
            polygons = [g for g in cut if g.geom_type == 'Polygon']
            cut = MultiPolygon(polygons) if polygons else border
        return cut

    def _get_multiline_from_members(self, members):
        lines = []
        for ref, kind, _role in members:
            if kind != 'w':
                continue
            way = self.ways.get(ref)
            if way is None:
                continue
            coords = CoordinateView(way[1])
            if len(coords) > 1:
                lines.append(LineString(list(coords)))
        if lines:
            return MultiLineString(lines)
        else:
            return None

    @classmethod
    def _get_center_node_id_from_members(cls, members):
        """
        A node to take the centre from, preferring one that is inside.

        ``label`` is a rendering hint: a point the mapper placed inside the
        polygon for a label to sit on. ``admin_centre`` is the seat of the
        administration, which need not be inside at all -- powiat krakowski
        surrounds Krakow and excludes it. Preferring admin_centre, as this
        did, put the seat in ``center`` and the denormalisation then read the
        county as being inside the city it encircles.
        """
        return cls._member_node_id(members, 'label') or \
            cls._member_node_id(members, 'admin_centre')

    @classmethod
    def _get_admin_centre_node_id_from_members(cls, members):
        """The seat, whether or not it lies inside the border."""
        return cls._member_node_id(members, 'admin_centre')

    @classmethod
    def _member_node_id(cls, members, role):
        for member in members:
            if member[1] == 'n' and member[2] == role:
                return member[0]
        return None

    def _seat_defaults(self, members):
        """Seat point, name and node id, for a relation that names one."""
        node_id = self._get_admin_centre_node_id_from_members(members)
        if node_id is None:
            return {}
        node = self.nodes.get(node_id)
        if node is None:
            return {}
        tags, coords = node
        return {
            'seat': Point(coords),
            'seat_name': tags.get('name') or '',
            'seat_osm_node_id': node_id,
        }

    def _get_merged_multiline_from_members(self, members):
        multiline = self._get_multiline_from_members(members)
        return self.__class__.merge_multiline(multiline)

    def _get_multiline_from_coords(self, coords):
        coords = list(coords)
        if len(coords) > 1:
            return MultiLineString(LineString(coords))
        return None

    @classmethod
    def _is_way_closed(cls, refs):
        return refs[0] == refs[-1]

    @classmethod
    def _is_valid_street_name(cls, name):
        """
        Check whether street name is not only road number like 'L 151' or 'A 2', ignore such streets.
        :return: boolean
        """
        chars_to_strip = [' ', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0']
        for char_to_strip in chars_to_strip:
            name = name.replace(char_to_strip, '')

        # we assume that we need at least 3 non numeric signs to create valid
        # street name
        if len(name) > 2:
            return True
        else:
            return False

    @classmethod
    def _merge_multipolygon(cls, multipolygon):
        """
        Always return MutliPolygon even if merging results in one Polygon.

        ``unary_union``, not ``cascaded_union``: the latter was deprecated in
        Django 3.1 and removed in 4.0.
        """
        multipolygon = multipolygon.unary_union
        if type(multipolygon) == Polygon:
            multipolygon = MultiPolygon(multipolygon)
        return multipolygon

    # Country importers decide what counts as a subarea / place / place subarea
    # from that country's admin_level conventions. They default to "nothing"
    # rather than being absent, so that a subclass which only overrides some of
    # them still runs instead of raising AttributeError halfway through a parse.
    @classmethod
    def _is_relation_countrysubarea(cls, osm_id, tags, members):
        return False

    @classmethod
    def _is_relation_place(cls, osm_id, tags, members):
        return False

    @classmethod
    def _is_relation_placesubarea(cls, osm_id, tags, members):
        return False

    @classmethod
    def _is_way_place(cls, osm_id, tags, refs):
        return False

    @classmethod
    def _is_node_place(cls, osm_id, tags, coords):
        return False

    @classmethod
    def _is_relation_street(cls, osm_id, tags, members):
        if 'type' in tags and tags['type'] == 'associatedStreet' and 'name' in tags \
                and cls._is_valid_street_name(tags['name']):
            return True
        return False

    @classmethod
    def _is_way_street(cls, osm_id, tags, refs):
        if len(refs) > 2 and 'highway' in tags and 'name' in tags and cls._is_valid_street_name(tags['name']):
            return True
        return False

    @classmethod
    def _is_address(cls, tags):
        """
        Anything with a house number and something to hang it on.

        ``addr:street`` covers 96% of Britain's addressed features and 65% of
        Poland's; ``addr:place`` covers the rest of Poland's -- village
        addresses where there is no street to name -- and almost none of
        Britain's. Both are wanted.
        """
        if not tags.get('addr:housenumber'):
            return False
        return bool(tags.get('addr:street') or tags.get('addr:place'))

    #: Digits at the start of a house number, whatever follows them.
    HOUSE_NUMBER_LEADING_DIGITS = re.compile(r'^\s*(\d+)')

    #: The largest value house_number_sort holds: a 32-bit integer, in
    #: PostgreSQL and in the OpenSearch mapping alike.
    HOUSE_NUMBER_SORT_MAX = 2 ** 31 - 1

    @classmethod
    def _house_number_sort_key(cls, number):
        """
        The integer a house number sorts by, or None when it has none.

        The leading integer is enough. Conventions differ in the suffix --
        4 bis, 2-pomp, 29-30, 24B, 3A -- and the leading integer orders all of
        them the way a person walking the street would: 29-30 after 28,
        4 bis after 4. Measured across seven extracts, 36 million addresses:

            Poland        8.58M   no leading digit 0.1%   ev.43
            Britain       5.11M                    0.2%   Unit 10, Inside WH Smith
            Netherlands   9.94M                    0.0%   B56, N4
            France        6.21M                    0.0%   A4, Le Capitole
            Denmark       2.61M                    0       --
            Norway        2.57M                    0.0%   one Cyrillic typo
            Finland       1.08M                    0.0%   A4, B 20, C 18 III

        Every country holds at 99.8% or better, so nothing is done per
        country; a country importer can override this method should one ever
        need it. The remainder is not house numbers -- units, landmarks,
        Finnish stairwells -- and stripping a leading letter would misplace
        them (A4 in France is a road), so they sort last instead.

        So do digits too long to be a house number -- a phone number or a
        property reference typed into addr:housenumber. The column and the
        index field are 32-bit, and one such value stopped Britain's import
        with "integer out of range".
        """
        match = cls.HOUSE_NUMBER_LEADING_DIGITS.match(number or '')
        if not match:
            return None
        key = int(match.group(1))
        return key if key <= cls.HOUSE_NUMBER_SORT_MAX else None

    @classmethod
    def _address_defaults(cls, tags):
        """
        ``name`` is what somebody would type: "Lakowa 5".

        Without a street it is what the number hangs on, ``addr:place``,
        before the town: a house on an estate is tagged with both, and named
        for the town "Osiedle Urocze 1" in Kraków was "Kraków 1" -- with 2,181
        more in Kraków and 3,810 in Poznań, none of them found by what is
        on the door. The town stays the settlement.

        No ``names``: the ``name:*`` tags of what is mapped at an address are
        those of the shop or the hotel, not of the address, and in another
        language "Marszałkowska 81" was "Żabka".

        The slug is derived from it and the parent, never disambiguated --
        there are 8.5 million of these for Poland alone.
        """
        street = tags.get('addr:street') or ''
        settlement = tags.get('addr:city') or tags.get('addr:place') or ''
        number = cls._fit_house_number(tags.get('addr:housenumber'))
        hung_on = street or tags.get('addr:place') or settlement
        return cls._fit_to_columns(Address, {
            'name': '{} {}'.format(hung_on, number).strip(),
            'names': {},
            'house_number': number,
            'house_number_sort': cls._house_number_sort_key(number),
            'street_name': street,
            'settlement_name': settlement,
            'postcode': tags.get('addr:postcode') or '',
        })

    @staticmethod
    def _fit_house_number(number):
        """
        One house number where the tag lists several.

        addr:housenumber is meant to be one number, and OSM separates a list
        with ";". Britain carries lists long enough to overflow the 64
        characters the column holds -- every flat of a block, one by one --
        and one of them stopped the import with "value too long". Its first
        number is the one the address is found by; the rest would be cut at
        an arbitrary character anyway.
        """
        if number and ';' in number:
            first = number.split(';', 1)[0].strip()
            return first or number
        return number

    @staticmethod
    def _fit_to_columns(model, values):
        """
        Cut each text value to its column's length.

        Free-text tags have no length limit in OSM and the columns do. One
        value too long fails the whole batch of thousands it is written with,
        so a value is shortened rather than lost along with its neighbours.
        """
        for field_name, value in values.items():
            if not isinstance(value, str):
                continue
            limit = getattr(model._meta.get_field(field_name), 'max_length', None)
            if limit and len(value) > limit:
                values[field_name] = value[:limit]
        return values

    @classmethod
    def _get_multipolygon_from_multiline(cls, multiline):
        polygons = []
        if multiline:
            lines = []
            for line in multiline:
                lines.append(list(line.coords))

            shapely_polygons, dangles, cuts, invalids = _load_polygonize_full()(lines)
            for shapely_polygon in shapely_polygons.geoms:
                polygon_exterior_coords = list(shapely_polygon.exterior.coords)
                polygon_holes = [list(interior.coords) for interior in shapely_polygon.interiors]
                try:
                    polygon = Polygon(polygon_exterior_coords, *polygon_holes)
                except (ValueError, TypeError, GEOSException):
                    # a ring that GEOS will not accept -- skip it rather than
                    # dropping into a debugger, which is what used to happen
                    continue
                polygons.append(
                    polygon
                )

            if polygons:
                multipolygon = MultiPolygon(polygons)
                return cls._merge_multipolygon(multipolygon)

        return None

    def _import_relations(self):
        items = self._progress(self.relations.items(), total=len(self.relations),
                               desc='relations', unit='rel')
        for osm_id, (tags, members) in items:
            self._import_relation(osm_id, tags, members)

    def _import_ways(self):
        items = self._progress(self.ways.items(), total=len(self.ways),
                               desc='ways', unit='way')
        for osm_id, (tags, coords) in items:
            self._import_way(osm_id, tags, CoordinateView(coords))

    def _import_nodes(self):
        # Only nodes that carry tags can become a Place; the rest were kept
        # solely to position relation centres.
        items = self._progress(self.nodes.items(), total=len(self.nodes),
                               desc='nodes', unit='node')
        for osm_id, (tags, coords) in items:
            if tags:
                self._import_node(osm_id, tags, coords)

    def _is_relation_to_cache(self, osm_id, tags, members):
        return any([
            self._is_relation_street(osm_id, tags, members),
            self._is_relation_place(osm_id, tags, members),
            self._is_relation_placesubarea(osm_id, tags, members),
            self._is_relation_countrysubarea(osm_id, tags, members),
        ])

    def _is_way_to_cache(self, osm_id, tags, refs):
        return any([
            self._is_way_street(osm_id, tags, refs),
            self._is_way_place(osm_id, tags, refs),
        ])

    def _is_node_to_cache(self, osm_id, tags, coords):
        return any([
            self._is_node_place(osm_id, tags, coords)
        ])

    @classmethod
    def _get_duplicated_placesubarea_names(cls, place):
        return place.subareas.values('name').annotate(Count('name')).filter(name__count__gt=1).values_list(
            'name',
            flat=True
        ).order_by()

    @classmethod
    def _merge_place_subareas(cls, place):
        duplicated_subarea_names = progress(
            cls._get_duplicated_placesubarea_names(place),
            desc='Doing merge of PlaceSubareas for {}...'.format(place),
            leave=False
        )
        for duplicated_subarea_name in duplicated_subarea_names:
            with transaction.atomic():
                duplicated_subareas = place.subareas.filter(name=duplicated_subarea_name).order_by('id').iterator()
                # next(it), not it.next(): the Python 2 spelling survived here
                # because no extract in the test set had duplicate place
                # subareas to merge
                merge_base = next(duplicated_subareas, None)
                if merge_base is None:
                    continue
                for duplicated_subarea in duplicated_subareas:
                    merge_base.border += duplicated_subarea.border
                    duplicated_subarea.mark_as_merged(into=merge_base)
                merge_base.border = cls._merge_multipolygon(merge_base.border)
                merge_base.save()

    @classmethod
    def _get_duplicated_street_tuples(cls, place):
        """
        Street names occurring more than once inside ``place``.

        ``.values('name')`` is what puts a GROUP BY on the query. Without it
        Count('name') is evaluated per row and is therefore always 1, so
        ``name_count__gt=1`` matches nothing and no street is ever merged --
        which is what happened when it was dropped in "python 3.x support".
        """
        return place.streets_within_border.values(
            'name'
        ).annotate(
            id_min=Min('id'),
            name_count=Count('name'),
            ids_array=ArrayAgg('id'),
            merged_lines=MergeMultiline('lines')
        ).filter(name_count__gt=1).values_list('name', 'id_min', 'ids_array', 'merged_lines').order_by()

    @classmethod
    def _merge_place_streets(cls, place):
        """
        One street of each name inside ``place``'s border -- and in each
        settlement of it, where the place is a municipality of villages each
        with its own: see utils.settlements.
        """
        from ..locations_recalculate_denormalized_fields import Command as Denorm

        streets_to_merge = progress(
            list(cls._get_duplicated_street_tuples(place)),
            desc='Doing merge of Streets for {}...'.format(place),
            leave=False
        )
        for name, id_min, ids_array, merged_lines in streets_to_merge:
            groups = streets_of(ids_array, Denorm.ADDRESS_MATCH_METRES, Denorm.MATCH_BOX_DEGREES)
            for group in groups:
                if len(group) < 2:
                    continue
                keeper = min(group)
                with transaction.atomic():
                    # mark as merged
                    Street.objects.filter(id__in=[i for i in group if i != keeper]).mark_as_merged(
                        into=keeper)
                    # save one item with lines merged
                    street = Street.objects.get(id=keeper)
                    street.lines = merged_lines if len(groups) == 1 else Street.objects.filter(
                        id__in=group).aggregate(lines=MergeMultiline('lines'))['lines']
                    street.save()

    @classmethod
    def _get_duplicated_place_tuples(cls):
        return Place.objects.values(
            'name'
        ).annotate(
            name_count=Count('name'),
            ids=array_agg_ordered('id', ['-area', '-population'])
        ).filter(name_count__gt=1).values_list('name', 'ids').order_by()

    #: How near a place of the same name must be, outside a border or to a
    #: point, to be the same place mapped twice: about 50 m, in degrees.
    #: Same-named hamlets in Poland stand 75 m apart and are two places;
    #: the three pairs nearer than 50 m are one place each, put down twice.
    SAME_SPOT = 0.0005

    @classmethod
    def _merge_places(cls):
        """
        Fold a place mapped twice into one: what lies inside a border of the
        same name, or on the same spot, is that place.

        A place with a border takes the places of its name inside it or
        within SAME_SPOT of it; a point takes only points within SAME_SPOT.
        A point used to take every place of its name within 25 km, a border
        included, so the town became the hamlet: Krosno (45,000 people) went
        into a hamlet 26 km away, and its streets with it; Poręba, Łazy,
        Żabno and ten more seats of gminas went the same way, and 929 places
        in Poland lost their border. Two points of one name are almost never
        one place: of 21,000 such pairs in the Polish extract within those
        25 km, 8 had the same TERYT locality code, and pairs 75 m apart had
        different ones.

        The border, not its bounding box: the village of Lipno lies 216 m
        outside the town of Lipno, inside the box.
        """
        # count() first rather than list(): the query can return a very large
        # number of groups and materialising them all just to show a progress
        # bar held the whole result set in memory alongside the caches.
        duplicated_places = cls._get_duplicated_place_tuples()
        places_to_merge = progress(
            duplicated_places.iterator(),
            total=duplicated_places.count(),
            desc='Doing merge of Places...',
            leave=False
        )
        for name, place_ids in places_to_merge:
            with transaction.atomic():
                for place_id in place_ids:
                    # make sure we have latest is_merged status
                    place = Place.objects.get(id=place_id)
                    if place.is_merged or not (place.border or place.center):
                        continue
                    all_but_place_ids = list(place_ids)
                    all_but_place_ids.remove(place.id)
                    duplicated_places = Place.objects.filter(
                        id__in=all_but_place_ids,
                        center__dwithin=(place.border or place.center, cls.SAME_SPOT),
                        is_merged=False
                    )
                    if not place.border:
                        # a border is never a point's double; it came first, larger
                        duplicated_places = duplicated_places.filter(border=None)
                    elif duplicated_places.count() > 0:
                        # when center is automatically generated populate center from
                        # merged place
                        if place.border.point_on_surface == place.center:
                            duplicated_place = duplicated_places.first()
                            place.center = duplicated_place.center
                            place.save(update_fields=['center'])

                    for duplicated_place in duplicated_places:
                        duplicated_place.mark_as_merged(into=place)

    def _import_countrysubarea_from_relation(self, osm_id, tags, members):
        if self._is_relation_countrysubarea(osm_id, tags, members) and self._are_all_members_cached(members):
            border = self._get_border_from_members(members)
            if border and not self._within_country(border):
                return
            if border:
                countrysubarea_defaults = {
                    'name': tags.get('name'),
                    'names': language_names(tags),
                    'border': border,
                    'admin_level': self._admin_level(tags),
                    'population': tags.get('population')
                }
                countrysubarea_defaults.update(self._seat_defaults(members))
                center_node_id = self._get_center_node_id_from_members(members)
                if center_node_id is not None:
                    center_node = self.nodes.get(center_node_id)
                    if center_node is not None:
                        _, coords = center_node
                        candidate = Point(coords)
                        # _presave_center replaces it if it falls outside
                        if border.contains(candidate):
                            countrysubarea_defaults['center'] = candidate
                self._upsert(CountrySubarea, 'relation_osm_id', osm_id, countrysubarea_defaults)
            else:
                if self._get_multiline_from_members(members):
                    tqdm.write(
                        "CountrySubarea not valid (doesn't have border but have multiline): "
                        "{} http://openstreetmap.org/relation/{}".format(tags.get('name'), osm_id)
                    )

    def _import_place_from_relation(self, osm_id, tags, members):
        if self._is_relation_place(osm_id, tags, members) and self._are_all_members_cached(members):
            border = self._get_border_from_members(members)
            if border and not self._within_country(border):
                return
            if border:
                population = tags.get('population', None)
                center_node_id = self._get_center_node_id_from_members(members)
                center_node_coords = None
                if center_node_id is not None:
                    center_node_tags, center_node_coords = self.nodes[center_node_id]
                    center_node_population = center_node_tags.get('population', None)
                    if center_node_population:
                        population = center_node_population
                place_defaults = {
                    'name': tags.get('name'),
                    'names': language_names(tags),
                    'border': border,
                    'admin_level': self._admin_level(tags),
                    'population': population
                }
                place_defaults.update(self._seat_defaults(members))
                if center_node_id is not None:
                    candidate = Point(center_node_coords)
                    # for a place the node is usually the settlement itself and
                    # sits inside; where it does not, _presave_center takes over
                    if border.contains(candidate):
                        place_defaults['center'] = candidate
                    self.already_imported_places_node_ids.add(center_node_id)
                self._upsert(Place, 'relation_osm_id', osm_id, place_defaults)
            else:
                if self._get_multiline_from_members(members):
                    tqdm.write(
                        "Place not valid (doesn't have border but have multiline): "
                        "{} http://openstreetmap.org/relation/{}".format(tags.get('name'), osm_id)
                    )

    def _import_place_from_way(self, osm_id, tags, coords):
        if self._is_way_place(osm_id, tags, coords):
            multiline = self._get_multiline_from_coords(coords)
            border = self._get_multipolygon_from_multiline(multiline)
            if border and not self._within_country(border):
                return
            if border:
                self._upsert(Place, 'way_osm_id', osm_id, {
                    'name': tags.get('name'),
                    'names': language_names(tags),
                    'border': border,
                    'population': tags.get('population')
                })
            else:
                if multiline:
                    tqdm.write(
                        "Place not valid (doesn't have border but have multiline): "
                        "{} http://openstreetmap.org/way/{}".format(tags.get('name'), osm_id)
                    )

    def _import_place_from_node(self, osm_id, tags, coords):
        if osm_id not in self.already_imported_places_node_ids and self._is_node_place(osm_id, tags, coords):
            if not self._within_country(Point(coords)):
                return
            self._upsert(Place, 'node_osm_id', osm_id, {
                'name': tags.get('name'),
                    'names': language_names(tags),
                'center': Point(coords),
                'population': tags.get('population')
            })

    def _import_placesubarea_from_relation(self, osm_id, tags, members):
        if self._is_relation_placesubarea(osm_id, tags, members) and self._are_all_members_cached(members):
            border = self._get_border_from_members(members)
            if border and not self._within_country(border):
                return
            if border:
                placesubarea_defaults = {
                    'name': tags.get('name'),
                    'names': language_names(tags),
                    'border': border,
                    'admin_level': self._admin_level(tags),
                    'population': tags.get('population')
                }
                placesubarea_defaults.update(self._seat_defaults(members))
                center_node_id = self._get_center_node_id_from_members(members)
                if center_node_id is not None:
                    center_node = self.nodes.get(center_node_id)
                    if center_node is not None:
                        _, coords = center_node
                        candidate = Point(coords)
                        if border.contains(candidate):
                            placesubarea_defaults['center'] = candidate
                self._upsert(PlaceSubarea, 'relation_osm_id', osm_id, placesubarea_defaults)
            else:
                if self._get_multiline_from_members(members):
                    tqdm.write(
                        "PlaceSubarea not valid (doesn't have border but have multiline): "
                        "{} http://openstreetmap.org/relation/{}".format(tags.get('name'), osm_id)
                    )

    def _import_street_from_relation(self, osm_id, tags, members):
        if self._is_relation_street(osm_id, tags, members) and self._are_all_members_cached(members):
            lines = self._get_merged_multiline_from_members(members)
            if not self._within_country(lines):
                return
            self._upsert(Street, 'relation_osm_id', osm_id, {
                'name': tags.get('name'),
                    'names': language_names(tags),
                'lines': lines,
            })

    def _import_street_from_way(self, osm_id, tags, coords):
        if self._is_way_street(osm_id, tags, coords):
            lines = self._get_multiline_from_coords(coords)
            if not self._within_country(lines):
                return
            self._upsert(Street, 'way_osm_id', osm_id, {
                'name': tags.get('name'),
                    'names': language_names(tags),
                'lines': lines,
            })

    def _import_relation(self, osm_id, tags, members):
        self._import_countrysubarea_from_relation(osm_id, tags, members)
        self._import_place_from_relation(osm_id, tags, members)
        self._import_placesubarea_from_relation(osm_id, tags, members)
        self._import_street_from_relation(osm_id, tags, members)

    def import_addresses(self):
        """
        Read addresses in a pass of their own, holding nothing.

        Poland has 8.58 million usable addresses and 62.6% of them are
        building outlines, whose node references come to 43.9 million. Caching
        those the way streets are cached is the memory profile that had
        systemd-oomd killing the desktop, and the geometry is wanted only to
        average into a single point. So this re-reads the extract instead: one
        more pass over the file, no cache, and the country border already
        assembled so each address can be filtered as it arrives.
        """
        start = datetime.now()
        print("Reading Addresses %s ..." % start)
        osmium = _load_osmium()

        kept = 0
        with _location_index() as storage:
            processor = (
                osmium.FileProcessor(self.file_path, osmium.osm.WAY | osmium.osm.NODE)
                .with_locations(storage)
                .with_filter(osmium.filter.KeyFilter('addr:housenumber'))
            )
            for obj in self._progress(processor, desc='addresses', unit='addr'):
                tags = {tag.k: tag.v for tag in obj.tags}
                if not self._is_address(tags):
                    continue
                point = self._address_point(obj)
                if point is None or not self._within_country(point):
                    continue
                defaults = self._address_defaults(tags)
                defaults['center'] = point
                field = 'node_osm_id' if obj.is_node() else 'way_osm_id'
                self._upsert(Address, field, obj.id, defaults)
                kept += 1

        self._flush_writes()
        end = datetime.now()
        print("Addresses read: %s, took: %s! (%d kept)\n" % (end, end - start, kept))

    @staticmethod
    def _address_point(obj):
        """
        Where the address is.

        A node is its own position. A building is the average of its outline,
        which is all that is kept of it -- an address has no extent worth
        storing.
        """
        if obj.is_node():
            return Point(obj.location.lon, obj.location.lat)
        points = [(n.location.lon, n.location.lat) for n in obj.nodes if n.location.valid()]
        if not points:
            return None
        return Point(sum(p[0] for p in points) / len(points),
                     sum(p[1] for p in points) / len(points))

    def _import_way(self, osm_id, tags, coords):
        self._import_place_from_way(osm_id, tags, coords)
        self._import_street_from_way(osm_id, tags, coords)

    def _import_node(self, osm_id, tags, coords):
        self._import_place_from_node(osm_id, tags, coords)

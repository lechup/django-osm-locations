import os
from array import array
from datetime import datetime

from django.conf import settings
from django.contrib.gis.geos import MultiLineString, LineString, Polygon, Point, MultiPolygon
from django.contrib.gis.geos.error import GEOSException
from django.contrib.postgres.aggregates.general import ArrayAgg
from django.db import transaction
from django.contrib.gis.db.models.aggregates import Union
from django.db.models import Count, Min
from tqdm import tqdm

from .. import Country, CountrySubarea, Place, PlaceSubarea, Street, MergeMultiline, array_agg_ordered
from ....utils.progress import progress, show_progress_default  # noqa: F401


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

    def __init__(self, model, lookup_field, batch_size=1000):
        self.model = model
        self.lookup_field = lookup_field
        self.batch_size = batch_size
        self._pending = {}
        self.created = 0
        self.updated = 0

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
    country_name = None
    country_slug = None
    iso2 = None
    iso3 = None
    un = None

    whitelist_tags = ['name', 'admin_level', 'boundary', 'place', 'highway', 'population',
                      'ISO3166-1', 'ISO3166-1:alpha2']
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
        # time. Keyed by the OSM id field each import path looks up on.
        batch_size = int(getattr(settings, 'LOCATIONS_WRITE_BATCH_SIZE', 1000) or 1000)
        self.writers = {
            (CountrySubarea, 'relation_osm_id'): BatchUpserter(CountrySubarea, 'relation_osm_id', batch_size),
            (Place, 'relation_osm_id'): BatchUpserter(Place, 'relation_osm_id', batch_size),
            (Place, 'way_osm_id'): BatchUpserter(Place, 'way_osm_id', batch_size),
            (Place, 'node_osm_id'): BatchUpserter(Place, 'node_osm_id', batch_size),
            (PlaceSubarea, 'relation_osm_id'): BatchUpserter(PlaceSubarea, 'relation_osm_id', batch_size),
            (Street, 'relation_osm_id'): BatchUpserter(Street, 'relation_osm_id', batch_size),
            (Street, 'way_osm_id'): BatchUpserter(Street, 'way_osm_id', batch_size),
        }

        # place can be imported from relation - it's center is node that later is imported also as new place
        # we use that cache to cache nodes_ids of relation centers, so that we won't have duplicates
        # see: https://www.openstreetmap.org/relation/3267973
        self.already_imported_places_node_ids = set()

    def do_import(self):
        self._parse_and_cache()

        # save cache to db
        start_all = datetime.now()
        print("Import started %s.\n" % start_all)

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

        end = datetime.now()
        print("Importing ended: %s, took: %s." % (end, end - start_all))

    def do_merge(self):
        """
        Use this method in child class to merge locations (eg. multiple entries of same street or city subarea)
        """
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
        self.writers[(model, lookup_field)].add(lookup_value, defaults)

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
    def _tags_to_dict(cls, obj):
        """OSM tags as a plain dict, reduced to the whitelist."""
        whitelist = cls.whitelist_tags
        return {tag.k: tag.v for tag in obj.tags if tag.k in whitelist}

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

        processor = (
            osmium.FileProcessor(self.file_path, osmium.osm.WAY | osmium.osm.NODE)
            .with_locations(_location_storage())
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
        multiline = self._get_multiline_from_members(self.country_boundary_members)
        border = self._get_multipolygon_from_multiline(multiline)
        if border is None:
            print("Could not assemble a border for %s; "
                  "importing everything this extract contains.\n" % self.country)
            return
        self.country_border = border
        self.country_boundary = border.prepared
        print("Country boundary assembled for %s.\n" % self.country)

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
            if key not in cls.whitelist_tags:
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
        for member in members:
            if member[1] == 'n' and member[2] == 'admin_centre':
                return member[0]
        for member in members:
            if member[1] == 'n' and member[2] == 'label':
                return member[0]
        return None

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
                    duplicated_subarea.mark_as_merged()
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
        streets_to_merge = progress(
            list(cls._get_duplicated_street_tuples(place)),
            desc='Doing merge of Streets for {}...'.format(place),
            leave=False
        )
        for name, id_min, ids_array, merged_lines in streets_to_merge:
            ids_array.remove(id_min)
            with transaction.atomic():
                # mark as merged
                Street.objects.filter(id__in=ids_array).mark_as_merged()
                # save one item with lines merged
                street = Street.objects.get(id=id_min)
                street.lines = merged_lines
                street.save()

    @classmethod
    def _get_duplicated_place_tuples(cls):
        return Place.objects.values(
            'name'
        ).annotate(
            name_count=Count('name'),
            ids=array_agg_ordered('id', ['-area'])
        ).filter(name_count__gt=1).values_list('name', 'ids').order_by()

    @classmethod
    def _merge_places(cls):
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
                    all_but_place_ids = list(place_ids)
                    all_but_place_ids.remove(place.id)
                    if not place.is_merged:
                        if place.border:
                            duplicated_places = Place.objects.filter(
                                id__in=all_but_place_ids,
                                center__intersects=place.bbox_polygon,
                                is_merged=False
                            )
                            if duplicated_places.count() > 0:
                                # when center is automatically generated populate center from
                                # merged place
                                if place.border.point_on_surface == place.center:
                                    duplicated_place = duplicated_places.first()
                                    place.center = duplicated_place.center
                                    place.save(update_fields=['center'])

                                for duplicated_place in duplicated_places:
                                    duplicated_place.mark_as_merged()
                        else:
                            duplicated_places = Place.objects.filter(
                                id__in=all_but_place_ids,
                                center__intersects=Place.get_polygon_from_bbox(
                                    place.near_bbox(km=25)
                                ),
                                is_merged=False
                            )
                            for duplicated_place in duplicated_places:
                                duplicated_place.mark_as_merged()

    def _import_countrysubarea_from_relation(self, osm_id, tags, members):
        if self._is_relation_countrysubarea(osm_id, tags, members) and self._are_all_members_cached(members):
            multiline = self._get_multiline_from_members(members)
            border = self._get_multipolygon_from_multiline(multiline)
            if border and not self._within_country(border):
                return
            if border:
                countrysubarea_defaults = {
                    'name': tags.get('name'),
                    'border': border,
                    'admin_level': self._admin_level(tags),
                    'population': tags.get('population')
                }
                center_node_id = self._get_center_node_id_from_members(members)
                if center_node_id is not None:
                    center_node = self.nodes.get(center_node_id)
                    _, coords = center_node
                    countrysubarea_defaults['center'] = Point(coords)
                self._upsert(CountrySubarea, 'relation_osm_id', osm_id, countrysubarea_defaults)
            else:
                if multiline:
                    tqdm.write(
                        "CountrySubarea not valid (doesn't have border but have multiline):",
                        tags.get('name'),
                        'http://openstreetmap.org/relation/{}'.format(osm_id)
                    )

    def _import_place_from_relation(self, osm_id, tags, members):
        if self._is_relation_place(osm_id, tags, members) and self._are_all_members_cached(members):
            multiline = self._get_multiline_from_members(members)
            border = self._get_multipolygon_from_multiline(multiline)
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
                    'border': border,
                    'admin_level': self._admin_level(tags),
                    'population': population
                }
                if center_node_id is not None:
                    place_defaults['center'] = Point(center_node_coords)
                    self.already_imported_places_node_ids.add(center_node_id)
                self._upsert(Place, 'relation_osm_id', osm_id, place_defaults)
            else:
                if multiline:
                    tqdm.write(
                        "Place not valid (doesn't have border but have multiline):",
                        tags.get('name'),
                        'http://openstreetmap.org/relation/{}'.format(osm_id)
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
                    'border': border,
                    'population': tags.get('population')
                })
            else:
                if multiline:
                    tqdm.write(
                        "Place not valid (doesn't have border but have multiline):",
                        tags.get('name'),
                        'http://openstreetmap.org/way/{}'.format(osm_id)
                    )

    def _import_place_from_node(self, osm_id, tags, coords):
        if osm_id not in self.already_imported_places_node_ids and self._is_node_place(osm_id, tags, coords):
            if not self._within_country(Point(coords)):
                return
            self._upsert(Place, 'node_osm_id', osm_id, {
                'name': tags.get('name'),
                'center': Point(coords),
                'population': tags.get('population')
            })

    def _import_placesubarea_from_relation(self, osm_id, tags, members):
        if self._is_relation_placesubarea(osm_id, tags, members) and self._are_all_members_cached(members):
            multiline = self._get_multiline_from_members(members)
            border = self._get_multipolygon_from_multiline(multiline)
            if border and not self._within_country(border):
                return
            if border:
                placesubarea_defaults = {
                    'name': tags.get('name'),
                    'border': border,
                    'admin_level': self._admin_level(tags),
                    'population': tags.get('population')
                }
                center_node_id = self._get_center_node_id_from_members(members)
                if center_node_id is not None:
                    center_node = self.nodes.get(center_node_id)
                    _, coords = center_node
                    placesubarea_defaults['center'] = Point(coords)
                self._upsert(PlaceSubarea, 'relation_osm_id', osm_id, placesubarea_defaults)
            else:
                if multiline:
                    tqdm.write(
                        "PlaceSubarea not valid (doesn't have border but have multiline):",
                        tags.get('name'),
                        'http://openstreetmap.org/relation/{}'.format(osm_id)
                    )

    def _import_street_from_relation(self, osm_id, tags, members):
        if self._is_relation_street(osm_id, tags, members) and self._are_all_members_cached(members):
            lines = self._get_merged_multiline_from_members(members)
            if not self._within_country(lines):
                return
            self._upsert(Street, 'relation_osm_id', osm_id, {
                'name': tags.get('name'),
                'lines': lines,
            })

    def _import_street_from_way(self, osm_id, tags, coords):
        if self._is_way_street(osm_id, tags, coords):
            lines = self._get_multiline_from_coords(coords)
            if not self._within_country(lines):
                return
            self._upsert(Street, 'way_osm_id', osm_id, {
                'name': tags.get('name'),
                'lines': lines,
            })

    def _import_relation(self, osm_id, tags, members):
        self._import_countrysubarea_from_relation(osm_id, tags, members)
        self._import_place_from_relation(osm_id, tags, members)
        self._import_placesubarea_from_relation(osm_id, tags, members)
        self._import_street_from_relation(osm_id, tags, members)

    def _import_way(self, osm_id, tags, coords):
        self._import_place_from_way(osm_id, tags, coords)
        self._import_street_from_way(osm_id, tags, coords)

    def _import_node(self, osm_id, tags, coords):
        self._import_place_from_node(osm_id, tags, coords)

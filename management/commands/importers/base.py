# -*- coding: utf-8 -*-
from collections import defaultdict
from datetime import datetime

from django.conf import settings
from django.contrib.gis.geos import MultiLineString, LineString, Polygon, MultiPolygon
from django.contrib.gis.geos.geometry import GEOSGeometry
from django.contrib.postgres.aggregates.general import ArrayAgg
from django.db import transaction, reset_queries, connection
from django.db.models import Count, Min
from django.utils.functional import cached_property
from imposm.parser import OSMParser
from shapely.ops import polygonize
from tqdm import tqdm

from .. import Country, CountrySubarea, Place, PlaceSubarea, Street, MergeMultiline
from ..caches.base import MemoryCache


class BaseImporter(object):
    whitelist_tags = set(('name', 'admin_level', 'boundary', 'place', 'highway'))
    location_models_mergable = [CountrySubarea, PlaceSubarea, Street]
    location_models_not_mergable = [Place]
    location_models = location_models_mergable + location_models_not_mergable

    def __init__(self, country_slug, file_path, verbose=0, cache_class=MemoryCache, cache_args=None, cache_kwargs=None):
        self.country = Country.objects.get(slug=country_slug)

        self.file_path = file_path
        self.verbose = verbose

        self._is_parsed_and_cached = False

        if cache_args is None:
            self.cache_args = []
        if cache_kwargs is None:
            self.cache_kwargs = {}

        self.nodes_cache = cache_class(*self.cache_args, **self.cache_kwargs)
        self.ways_cache = cache_class(*self.cache_args, **self.cache_kwargs)
        self.relations_cache = cache_class(*self.cache_args, **self.cache_kwargs)

    def mark_as_parsed_and_cached(self):
        self._is_parsed_and_cached = True

    def parse_and_create_cache(self):
        """
        parse osm data file and store it in cache by using imposm.parser callbacks
        http://imposm.org/docs/imposm.parser/latest/concepts.html
        we store parsed data in 3 caches (relations_cache, nodes_cache, ways_cache)
        we need to make 3 passes to get whole hierarchy: relations -> ways -> nodes
        """

        start_all = datetime.now()
        print("Parsing for %s started %s.\n" % (self.country, start_all))

        # fist pass, cache relations
        start = datetime.now()
        print("Creating cache for Relations %s ..." % start)
        p = OSMParser(concurrency=2, relations_callback=self._cache_relations,
                      relations_tag_filter=self._tag_filter)
        p.parse(self.file_path)
        end = datetime.now()
        print("Relations cache ended: %s, took: %s!\n" % (end, end - start))

        # second pass, cache ways
        start = datetime.now()
        print("Creating cache for Ways %s ..." % start)
        p = OSMParser(concurrency=2, ways_callback=self._cache_ways, ways_tag_filter=self._tag_filter)
        p.parse(self.file_path)
        end = datetime.now()
        print("Ways cache ended: %s, took: %s!\n" % (end, end - start))

        # third pass, cache nodes / coords
        start = datetime.now()
        print("Creating cache for Coords and Nodes %s ..." % start)
        p = OSMParser(concurrency=2, coords_callback=self._save_coords_in_cache,
                      nodes_callback=self._cache_nodes)
        p.parse(self.file_path)
        end = datetime.now()
        print("Coords and Nodes cache ended: %s, took: %s!\n" % (end, end - start))

        end = datetime.now()
        print("Parsing ended: %s, took: %s!\n" % (end, end - start_all))

        self.mark_as_parsed_and_cached()

    def do_import(self):
        if not self._is_parsed_and_cached:
            self.parse_and_create_cache()

        # save cache to db
        start_all = datetime.now()
        print("Import started %s.\n" % start_all)

        start = datetime.now()
        print("Importing Relations %s ..." % start)
        with transaction.atomic():
            self._import_relations()
        end = datetime.now()
        print("Importing Relations ended: %s, took: %s!\n" % (end, end - start))

        start = datetime.now()
        print("Importing Ways %s ..." % start)
        with transaction.atomic():
            self._import_ways()
        end = datetime.now()
        print("Importing Ways ended: %s, took: %s!\n" % (end, end - start))

        start = datetime.now()
        print("Importing Coords and Nodes %s ..." % start)
        with transaction.atomic():
            self._import_nodes()
        end = datetime.now()
        print("Importing Coords and Nodes ended: %s, took: %s!\n" % (end, end - start))

        end = datetime.now()
        print("Importing ended: %s, took: %s." % (end, end - start_all))

    def do_unmerge(self):
        """
        Use this method to unmerge locations (eg. before updating from new country OSM file)
        """
        for model_class in tqdm(self.location_models_mergable, desc='Doing unmerge...', leave=False):
            model_class.objects_all.all().mark_as_not_merged()

    def do_merge(self):
        """
        Use this method in child class to merge locations (eg. multiple entries of same street or city subarea)
        """
        pass

    @cached_property
    def location_osm_ids_from_db_cache(self):
        cache = defaultdict(dict)
        for model_class in self.location_models:
            cache[model_class.__name__]['node_osm_id'] = set(
                self.location_objects_from_db_cache[model_class.__name__]['node_osm_id'].keys()
            )
            cache[model_class.__name__]['way_osm_id'] = set(
                self.location_objects_from_db_cache[model_class.__name__]['way_osm_id'].keys()
            )
            cache[model_class.__name__]['relation_osm_id'] = set(
                self.location_objects_from_db_cache[model_class.__name__]['relation_osm_id'].keys()
            )
        return cache

    @cached_property
    def location_objects_from_db_cache(self):
        cache = defaultdict(dict)
        for model_class in self.location_models:
            model_class_objects = getattr(model_class, 'objects_all', getattr(model_class, 'objects'))
            cache[model_class.__name__]['node_osm_id'] = {}
            cache[model_class.__name__]['way_osm_id'] = {}
            cache[model_class.__name__]['relation_osm_id'] = {}

            for model_class_object in model_class_objects.exclude(node_osm_id=None).iterator():
                cache[model_class.__name__]['node_osm_id'][model_class_object.node_osm_id] = model_class_object
            for model_class_object in model_class_objects.exclude(way_osm_id=None).iterator():
                cache[model_class.__name__]['way_osm_id'][model_class_object.way_osm_id] = model_class_object
            for model_class_object in model_class_objects.exclude(relation_osm_id=None).iterator():
                cache[model_class.__name__]['relation_osm_id'][model_class_object.relation_osm_id] = model_class_object
        return cache

    def create_or_update(self, model_class, model_kwargs):
        def update_object(obj, **kwargs):
            for k, v in kwargs.items():
                setattr(obj, k, v)
            obj.save(update_fields=kwargs.keys())

        for model_kwargs_key in model_kwargs.keys():
            if model_kwargs_key.find('_osm_id') != -1:
                osm_ids_cache_name = model_kwargs_key
                break

        osm_id = model_kwargs.get(osm_ids_cache_name)

        if osm_id in self.location_osm_ids_from_db_cache[model_class.__name__][osm_ids_cache_name]:
            model_class_object = self.location_objects_from_db_cache[model_class.__name__][osm_ids_cache_name][osm_id]
            update_object(model_class_object, **model_kwargs)
        else:
            model_class.objects.create(**model_kwargs).save()

        # do not store queries for debugging even if debug mode is enabled
        # to avoid memory consumption...
        if settings.DEBUG:
            reset_queries()

    def _are_all_members_cached(self, members):
        """
            Check if all @members are in country osm data vel in cache
            :param members: list of members eg. [(123, 'way', 'outer'), (234, 'node', 'inner') ...]
            :rtype : bool
        """
        for member in members:
            if member[1] == 'node':
                if self.nodes_cache.exists(member[0]):
                    if self.nodes_cache.get(member[0]) is None:
                        return False
                else:
                    return False
            if member[1] == 'way':
                if self.ways_cache.exists(member[0]):
                    if self.ways_cache.get(member[0]) is None:
                        return False
                else:
                    return False
        return True

    def _are_all_refs_cached(self, refs):
        """
            Check if all @refs are in country osm data vel in cache
            :param refs: list of refs eg. [123, 234, 345, ...]
            :rtype : bool
        """
        for ref in refs:
            if self.nodes_cache.exists(ref):
                if self.nodes_cache.get(ref) is None:
                    return False
            else:
                return False
        return True

    def _get_multipolygon_from_members(self, members):
        return self.__class__._get_multipolygon_from_multiline(
            self._get_multiline_from_members(members)
        )

    def _get_multipolygon_from_refs(self, refs):
        return self.__class__._get_multipolygon_from_multiline(
            self._get_multiline_from_refs(refs)
        )

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
        for member in members:
            if member[1] == 'way':
                lines.append(
                    LineString(
                        self.get_coords_from_way_refs(self.ways_cache.get(member[0])[1])
                    )
                )
        if lines:
            return MultiLineString(lines)
        else:
            return None

    @classmethod
    def _get_admin_centre_node_id_from_members(cls, members):
        for member in members:
            if member[1] == 'node' and member[2] == 'admin_centre':
                return member[0]
        return None

    def _get_merged_multiline_from_members(self, members):
        multiline = self._get_multiline_from_members(members)
        return self.__class__.merge_multiline(multiline)

    def _get_multiline_from_refs(self, refs):
        coords = self.get_coords_from_way_refs(refs)
        if coords:
            return MultiLineString(
                LineString(coords)
            )
        else:
            return None

    def get_coords_from_way_refs(self, refs):
        coords = []
        for ref in refs:
            coords.append(self.nodes_cache.get(ref)[1])
        return coords

    @staticmethod
    def is_way_closed(refs):
        return refs[0] == refs[-1]

    @classmethod
    def merge_multipolygon(cls, multipolygon):
        """
        Always return MutliPolygon even if merging results in one Polygon.
        """
        multipolygon = multipolygon.cascaded_union
        if type(multipolygon) == Polygon:
            multipolygon = MultiPolygon(multipolygon)
        return multipolygon

    @classmethod
    def _get_multipolygon_from_multiline(cls, multiline):
        polygons = []
        if multiline:
            lines = []
            for line in multiline:
                lines.append(line.coords[:])

            for polygon in polygonize(lines):
                # that can be MultiLineString or LineString
                polygon_boundaries = GEOSGeometry(polygon.boundary.to_wkt())
                if type(polygon_boundaries) is not MultiLineString:
                    polygon_boundaries = [polygon_boundaries]
                for polygon_boundary in polygon_boundaries:
                    polygons.append(
                        Polygon(polygon_boundary.coords[:])
                    )

            if polygons:
                multipolygon = MultiPolygon(polygons)
                return cls.merge_multipolygon(multipolygon)

        return None

    def _cache_relations(self, relations):
        """
            Callback for parsing Relation and storing in cache.
            Relations are saved as (tags, [members]) where:
            - tags: {'name': 'Whatever'}
            - members: [(123, 'way', 'outer'), (234, 'node', 'inner') ...]
        """
        # print 'starting relations_callback %d...' % len(relations)
        for osm_id, tags, members in relations:
            if self._is_relation_to_cache(osm_id, tags, members):
                self._cache_relation(osm_id, tags, members)

    def _cache_relation(self, osm_id, tags, members):
        self.relations_cache.set(osm_id, (tags, members))
        for member in members:
            if member[1] == 'node':
                self.nodes_cache.precache.add(member[0])
            elif member[1] == 'way':
                self.ways_cache.precache.add(member[0])

    def _cache_ways(self, ways):
        """
            Callback for parsing Ways and storing in redis cache.
            Ways are saved as ({tags}, [refs])
        """
        for osm_id, tags, refs in ways:
            # save ways needed by relations
            if osm_id in self.ways_cache.precache:
                self._cache_way(osm_id, tags, refs)
            # save ways which describe part of locations
            else:
                if self._is_way_to_cache(osm_id, tags, refs):
                    self._cache_way(osm_id, tags, refs)

    def _cache_way(self, osm_id, tags, refs):
        self.ways_cache.set(osm_id, (tags, refs))
        for ref in refs:
            self.nodes_cache.precache.add(ref)

    def _cache_nodes(self, nodes):
        """
            Callback for parsing Coords (Nodes without tags) and storing in redis cache.
            Coords are saved as (x, y)
        """
        for osm_id, tags, coords in nodes:
            if osm_id in self.nodes_cache.precache:
                self._cache_node(osm_id, tags, coords)
            else:
                if self._is_node_to_cache(osm_id, tags, coords):
                    self._cache_node(osm_id, tags, coords)

    def _cache_node(self, osm_id, tags, coords):
        self.nodes_cache.set(osm_id, (tags, coords))

    def _save_coords_in_cache(self, coords):
        """
            Callback for parsing Coords (Nodes without tags) and storing in redis cache.
            Coords are saved as (x, y)
        """
        for osm_id, x, y in coords:
            if osm_id in self.nodes_cache.precache:
                self.nodes_cache.set(osm_id, ({}, (x, y)))

    def _import_relations(self):
        for osm_id in self.relations_cache.keys():
            tags, members = self.relations_cache.get(osm_id)
            self._import_relation(osm_id, tags, members)

    def _import_ways(self):
        for osm_id in self.ways_cache.keys():
            tags, refs = self.ways_cache.get(osm_id)
            self._import_way(osm_id, tags, refs)

    def _import_nodes(self):
        for osm_id in self.nodes_cache.keys():
            if osm_id != '0':
                tags, coords = self.nodes_cache.get(osm_id)
                self._import_node(osm_id, tags, coords)

    def _import_relation(self, osm_id, tags, members):
        raise NotImplementedError

    def _import_way(self, osm_id, tags, refs):
        raise NotImplementedError

    def _import_node(self, osm_id, tags, coords):
        raise NotImplementedError

    def _is_relation_to_cache(self, osm_id, tags, members):
        raise NotImplementedError

    def _is_way_to_cache(self, osm_id, tags, refs):
        raise NotImplementedError

    def _is_node_to_cache(self, osm_id, tags, coords):
        raise NotImplementedError

    @classmethod
    def _tag_filter(cls, tags):
        for key in tags.keys():
            if key not in cls.whitelist_tags:
                del tags[key]
        if 'name' in tags and len(tags) == 1:
            # tags with only a name have no information
            # how to handle this element
            del tags['name']

    @classmethod
    def _get_duplicated_citysubarea_names(cls, city):
        return city.subareas.values('name').annotate(Count('name')).filter(name__count__gt=1).values_list(
            'name',
            flat=True
        ).order_by()

    @classmethod
    def _merge_place_subareas(cls, place):
        duplicated_subarea_names = tqdm(
            cls._get_duplicated_citysubarea_names(place),
            desc=u'Doing merge of PlaceSubareas for {}...'.format(place),
            leave=False
        )
        for duplicated_subarea_name in duplicated_subarea_names:
            with transaction.atomic():
                duplicated_subareas = place.subareas.filter(name=duplicated_subarea_name).order_by('id').iterator()
                merge_base = duplicated_subareas.next()
                tqdm.write(u"{}, PlaceSubarea {} is duplicated - merging!".format(place, duplicated_subarea_name))
                for duplicated_subarea in duplicated_subareas:
                    merge_base.border += duplicated_subarea.border
                    duplicated_subarea.mark_as_merged()
                merge_base.border = cls.merge_multipolygon(merge_base.border)
                merge_base.save()

    @classmethod
    def _get_duplicated_street_tuples(cls, place):
        return place.streets.values(
            'name'
        ).annotate(
            id_min=Min('id'),
            name_count=Count('name'),
            ids_array=ArrayAgg('id'),
            merged_lines=MergeMultiline('lines')
        ).filter(name_count__gt=1).values_list('name', 'id_min', 'ids_array', 'merged_lines').order_by()

    @classmethod
    def _merge_streets(cls, place):
        streets_to_merge = tqdm(
            list(cls._get_duplicated_street_tuples(place)),
            desc=u'Doing merge of Streets for {}...'.format(place),
            leave=False
        )
        for name, id_min, ids_array, merged_lines in streets_to_merge:
            tqdm.write(u"{}, Street {} is duplicated - merging!".format(place, name))
            ids_array.remove(id_min)
            with transaction.atomic():
                # mark as merged
                Street.objects_all.filter(id__in=ids_array).mark_as_merged()
                # save one item with lines merged
                Street.objects_all.filter(id=id_min).update(lines=merged_lines)

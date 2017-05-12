# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from collections import defaultdict
from datetime import datetime

from django.conf import settings
from django.contrib.gis.geos import MultiLineString, LineString, Polygon, MultiPolygon
from django.contrib.gis.geos.geometry import GEOSGeometry
from django.contrib.postgres.aggregates.general import ArrayAgg
from django.db import transaction, reset_queries
from django.db.models import Count, Min
from django.utils.functional import cached_property
from imposm.parser import OSMParser
from shapely.ops import polygonize
from tqdm import tqdm

from .. import Country, CountrySubarea, Place, PlaceSubarea, Street, MergeMultiline
from ..caches.base import MemoryCache, MemcachedCache, DiskCache, FileCache


if getattr(settings, 'LOCATIONS_MEMCACHED_CACHE', False):
    CACHE_CLASS = MemcachedCache
elif getattr(settings, 'LOCATIONS_DISK_CACHE', False):
    CACHE_CLASS = DiskCache
elif getattr(settings, 'LOCATIONS_FILE_CACHE', False):
    CACHE_CLASS = FileCache
else:
    CACHE_CLASS = MemoryCache


class BaseImporter(object):
    whitelist_tags = set(['name', 'admin_level', 'boundary', 'place', 'highway'])
    location_models_mergable = [CountrySubarea, PlaceSubarea, Street]
    location_models_not_mergable = [Place]
    location_models = location_models_mergable + location_models_not_mergable

    def __init__(self, country_slug, file_path, verbose=0):
        self.country = Country.objects.get(slug=country_slug)

        self.file_path = file_path
        self.verbose = verbose

        self.nodes_cache = CACHE_CLASS()
        self.ways_cache = CACHE_CLASS()
        self.relations_cache = CACHE_CLASS()

    def do_import(self):
        self._parse_and_cache()

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

        self.do_update_country_border()

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

    def do_update_country_border(self):
        print("Update'ing %s country border....\n" % (self.__class__.__name__,))
        country_border = MultiPolygon([])
        country_subarea_count = CountrySubarea.objects.count()
        for country_subarea in tqdm(CountrySubarea.objects.all().iterator(), total=country_subarea_count):
            country_border += country_subarea.border
        self.country.border = self._merge_multipolygon(country_border)
        self.country.save()

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

    def _parse_and_cache(self):
        """
        parse osm data file and store it in cache by using imposm.parser callbacks
        http://imposm.org/docs/imposm.parser/latest/concepts.html
        we store parsed data in 3 caches (relations_cache, ways_cache, nodes_cache)
        we need to make 3 passes to get whole hierarchy: relations -> ways -> nodes/coords
        """

        start_all = datetime.now()
        print("Parsing for %s started %s.\n" % (self.country, start_all))

        self._parse_and_cache_relations()
        self._parse_and_cache_ways()
        self._parse_and_cache_coords()
        self._parse_and_cache_nodes()

        end = datetime.now()
        print("Parsing ended: %s, took: %s!\n" % (end, end - start_all))

    def _parse_and_cache_relations(self):
        start = datetime.now()
        print("Creating cache for Relations %s ..." % start)
        p = OSMParser(
            concurrency=2,
            relations_callback=self._cache_relations
        )
        p.parse(self.file_path)
        del p
        end = datetime.now()
        print("Relations cache ended: %s, took: %s!\n" % (end, end - start))

    def _parse_and_cache_ways(self):
        start = datetime.now()
        print("Creating cache for Ways %s ..." % start)
        p = OSMParser(
            concurrency=2,
            ways_callback=self._cache_ways
        )
        p.parse(self.file_path)
        del p
        end = datetime.now()
        print("Ways cache ended: %s, took: %s!\n" % (end, end - start))

    def _parse_and_cache_coords(self):
        start = datetime.now()
        print("Creating cache for Coords %s ..." % start)
        p = OSMParser(
            concurrency=2,
            coords_callback=self._cache_coords,
        )
        p.parse(self.file_path)
        del p
        end = datetime.now()
        print("Coords cache ended: %s, took: %s!\n" % (end, end - start))

    def _parse_and_cache_nodes(self):
        start = datetime.now()
        print("Creating cache for Nodes %s ..." % start)
        p = OSMParser(
            concurrency=2,
            nodes_callback=self._cache_nodes,
            nodes_tag_filter=self._tag_filter
        )
        p.parse(self.file_path)
        del p
        end = datetime.now()
        print("Nodes cache ended: %s, took: %s!\n" % (end, end - start))

    @classmethod
    def _tag_filter(cls, tags):
        # reduce memory usage
        for key in list(tags):
            if key not in cls.whitelist_tags:
                del tags[key]

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
                        self._get_coords_from_way_refs(self.ways_cache.get(member[0])[1])
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
        coords = self._get_coords_from_way_refs(refs)
        if coords:
            return MultiLineString(
                LineString(coords)
            )
        else:
            return None

    def _get_coords_from_way_refs(self, refs):
        coords = []
        for ref in refs:
            coords.append(self.nodes_cache.get(ref)[1])
        return coords

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
        """
        multipolygon = multipolygon.cascaded_union
        if type(multipolygon) == Polygon:
            multipolygon = MultiPolygon(multipolygon)
        return multipolygon

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
                return cls._merge_multipolygon(multipolygon)

        return None

    def _cache_relations(self, relations):
        """
            Callback for parsing Relation and storing in cache.
            Relations are saved as (tags, [members]) where:
            - tags: {'name': 'Whatever'}
            - members: [(123, 'way', 'outer'), (234, 'node', 'inner') ...]
        """
        for osm_id, tags, members in relations:
            if self._is_relation_to_cache(osm_id, tags, members):
                self._cache_relation(osm_id, tags, members)
        self.relations_cache.commit()

    def _cache_relation(self, osm_id, tags, members):
        self.relations_cache.set(osm_id, (tags, members))
        for member in members:
            if member[1] == 'node':
                self.nodes_cache.precache.add(member[0])
            elif member[1] == 'way':
                self.ways_cache.precache.add(member[0])

    def _cache_ways(self, ways):
        """
            Callback for parsing Ways and storing in cache.
            Ways are saved as ({tags}, [refs])
        """
        for osm_id, tags, refs in ways:
            # save ways needed by relations or save ways which describe part of locations
            if osm_id in self.ways_cache.precache or self._is_way_to_cache(osm_id, tags, refs):
                self._cache_way(osm_id, tags, refs)
        self.ways_cache.commit()

    def _cache_way(self, osm_id, tags, refs):
        self.ways_cache.set(osm_id, (tags, refs))
        for ref in refs:
            self.nodes_cache.precache.add(ref)

    def _cache_nodes(self, nodes):
        """
            Callback for parsing Coords (Nodes without tags) and storing in cache.
            Coords are saved as (x, y)
        """
        for osm_id, tags, coords in nodes:
            if osm_id != '0':
                # save nodes needed by relations or ways or save nodes which describe part of locations
                if osm_id in self.nodes_cache.precache or self._is_node_to_cache(osm_id, tags, coords):
                    self._cache_node(osm_id, tags, coords)
        self.nodes_cache.commit()

    def _cache_node(self, osm_id, tags, coords):
        self.nodes_cache.set(osm_id, (tags, coords))

    def _cache_coords(self, coords):
        """
            Callback for parsing Coords (Nodes without tags) and storing in cache.
            Coords are saved as (x, y) with empty tags in nodes cache.
        """
        for osm_id, x, y in coords:
            # save coords needed by relations or ways
            if osm_id in self.nodes_cache.precache:
                self.nodes_cache.set(osm_id, ({}, (x, y)))
        self.nodes_cache.commit()

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
            tags, coords = self.nodes_cache.get(osm_id)
            self._import_node(osm_id, tags, coords)

    def _import_relation(self, osm_id, tags, members):
        raise NotImplementedError

    def _import_way(self, osm_id, tags, refs):
        raise NotImplementedError

    def _import_node(self, osm_id, tags, coords):
        raise NotImplementedError

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
    def _get_duplicated_citysubarea_names(cls, city):
        return city.subareas.values('name').annotate(Count('name')).filter(name__count__gt=1).values_list(
            'name',
            flat=True
        ).order_by()

    @classmethod
    def _merge_place_subareas(cls, place):
        duplicated_subarea_names = tqdm(
            cls._get_duplicated_citysubarea_names(place),
            desc='Doing merge of PlaceSubareas for {}...'.format(place),
            leave=False
        )
        for duplicated_subarea_name in duplicated_subarea_names:
            with transaction.atomic():
                duplicated_subareas = place.subareas.filter(name=duplicated_subarea_name).order_by('id').iterator()
                merge_base = duplicated_subareas.next()
                tqdm.write('{}, PlaceSubarea {} is duplicated - merging!' .format(place, duplicated_subarea_name))
                for duplicated_subarea in duplicated_subareas:
                    merge_base.border += duplicated_subarea.border
                    duplicated_subarea.mark_as_merged()
                merge_base.border = cls._merge_multipolygon(merge_base.border)
                merge_base.save()

    @classmethod
    def _get_duplicated_street_tuples(cls, place):
        return place.streets_within_border.values(
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
            desc='Doing merge of Streets for {}...'.format(place),
            leave=False
        )
        for name, id_min, ids_array, merged_lines in streets_to_merge:
            tqdm.write('{}, Street {} is duplicated - merging!'.format(place, name))
            ids_array.remove(id_min)
            with transaction.atomic():
                # mark as merged
                Street.objects_all.filter(id__in=ids_array).mark_as_merged()
                # save one item with lines merged
                Street.objects_all.filter(id=id_min).update(lines=merged_lines)

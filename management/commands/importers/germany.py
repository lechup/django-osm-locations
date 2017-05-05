# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.contrib.gis.geos import Point, MultiPolygon
from tqdm import tqdm

from .base import BaseImporter
from .. import CountrySubarea, Place, PlaceSubarea, Street


class GermanyImporter(BaseImporter):
    def do_merge(self):
        # merge CountrySubareas like: http://www.openstreetmap.org/relation/62745
        # with places like: http://www.openstreetmap.org/node/240038130
        country_subareas = self.country.subareas.exclude(border=None).iterator()
        country_subareas_count = self.country.subareas.exclude(border=None).count()
        for country_subarea in tqdm(country_subareas, total=country_subareas_count,
                                    desc='Doing merge of CountrySubareas...'):
            self._merge_country_subareas(country_subarea)

        # remove duplicates from locations below places eg:
        # streets (one street can be saved as more than one line in OSM)
        places = self.country.places.exclude(border=None).iterator()
        places_count = self.country.places.exclude(border=None).count()
        for place in tqdm(places, total=places_count,
                desc='Doing merge of Places...'):
            self._merge_streets(place)

    @classmethod
    def _merge_country_subareas(cls, country_subarea):
        try:
            place = Place.objects.get(name=country_subarea.name, center=country_subarea.center)
        except Place.DoesNotExist:
            pass
        except Place.MultipleObjectsReturned:
            tqdm.write('{}, returned more than one result...'.format(country_subarea))
        else:
            tqdm.write('{}, is also a Place - merged!'.format(country_subarea))
            place.border = country_subarea.border
            place.save()
            country_subarea.mark_as_merged()

    @classmethod
    def _is_relation_countrysubarea(cls, osm_id, tags, members):
        if 'admin_level' in tags and tags['admin_level'] in ['4', '5', '6', '7'] and 'name' in tags and \
           'boundary' in tags and tags['boundary'] == 'administrative':
            return True
        return False

    @classmethod
    def _is_relation_place(cls, osm_id, tags, members):
        if 'admin_level' in tags and tags['admin_level'] in ['8'] and 'name' in tags and \
           'boundary' in tags and tags['boundary'] == 'administrative':
            return True
        return False

    @classmethod
    def _is_way_place(cls, osm_id, tags, refs):
        if len(refs) > 2 and cls._is_way_closed(refs) and 'place' in tags and\
                tags['place'] in set(('city', 'village', 'hamlet', 'town')) and 'name' in tags:
            return True
        return False

    @classmethod
    def _is_node_place(cls, osm_id, tags, coords):
        if 'place' in tags and tags['place'] in set(('city', 'village', 'hamlet', 'town')) and 'name' in tags:
            return True
        return False

    @classmethod
    def _is_relation_placesubarea(cls, osm_id, tags, members):
        if 'admin_level' in tags and tags['admin_level'] in ['9', '10'] and 'name' in tags and \
           'boundary' in tags and tags['boundary'] == 'administrative':
            return True
        return False

    def _import_countrysubarea_from_relation(self, osm_id, tags, members):
        if self._is_relation_countrysubarea(osm_id, tags, members) and self._are_all_members_cached(members):
            border = self._get_multipolygon_from_members(members)
            admin_centre_node_id = self._get_admin_centre_node_id_from_members(members)
            if border:
                print(tags['name'], osm_id)
                create_or_update_dict = {
                    'relation_osm_id': osm_id,
                    'name': tags['name'],
                    'border': border,
                }
                if admin_centre_node_id is not None:
                    admin_centre_node = self.nodes_cache.get(admin_centre_node_id)
                    _, coords = admin_centre_node
                    create_or_update_dict['center'] = Point(coords)
                self.create_or_update(CountrySubarea, create_or_update_dict)
            return True
        return False

    def _import_place_from_relation(self, osm_id, tags, members):
        if self._is_relation_place(osm_id, tags, members) and self._are_all_members_cached(members):
            border = self._get_multipolygon_from_members(members)
            admin_centre_node_id = self._get_admin_centre_node_id_from_members(members)
            if border:
                print(tags['name'], osm_id)
                create_or_update_dict = {
                    'relation_osm_id': osm_id,
                    'name': tags['name'],
                    'border': border,
                }
                if admin_centre_node_id is not None:
                    admin_centre_node = self.nodes_cache.get(admin_centre_node_id)
                    _, coords = admin_centre_node
                    create_or_update_dict['center'] = Point(coords)
                self.create_or_update(Place, create_or_update_dict)
            return True
        return False

    def _import_place_from_way(self, osm_id, tags, refs):
        if self._is_way_place(osm_id, tags, refs) and self._are_all_refs_cached(refs):
            border = self._get_multipolygon_from_refs(refs)
            if border:
                print(tags['name'], osm_id)
                self.create_or_update(Place, {
                    'way_osm_id': osm_id,
                    'name': tags['name'],
                    'border': border,
                })
            return True
        return False

    def _import_place_from_node(self, osm_id, tags, coords):
        if self._is_node_place(osm_id, tags, coords):
            print(tags['name'], osm_id)
            self.create_or_update(Place, {
                'node_osm_id': osm_id,
                'name': tags['name'],
                'center': Point(coords)
            })
            return True
        return False

    def _import_placesubarea_from_relation(self, osm_id, tags, members):
        if self._is_relation_placesubarea(osm_id, tags, members) and self._are_all_members_cached(members):
            border = self._get_multipolygon_from_members(members)
            admin_centre_node_id = self._get_admin_centre_node_id_from_members(members)
            if border:
                print(tags['name'], osm_id)

                create_or_update_dict = {
                    'relation_osm_id': osm_id,
                    'name': tags['name'],
                    'border': border,
                }
                if admin_centre_node_id is not None:
                    admin_centre_node = self.nodes_cache.get(admin_centre_node_id)
                    _, coords = admin_centre_node
                    create_or_update_dict['center'] = Point(coords)

                self.create_or_update(PlaceSubarea, create_or_update_dict)
            return True
        return False

    def _import_street_from_relation(self, osm_id, tags, members):
        if self._is_relation_street(osm_id, tags, members) and self._are_all_members_cached(members):
            print(tags['name'], osm_id)
            self.create_or_update(Street, {
                'relation_osm_id': osm_id,
                'name': tags['name'],
                'lines': self._get_merged_multiline_from_members(members),
            })
            return True
        return False

    def _import_street_from_way(self, osm_id, tags, refs):
        if self._is_way_street(osm_id, tags, refs) and self._are_all_refs_cached(refs):
            print(tags['name'], osm_id)
            self.create_or_update(Street, {
                'way_osm_id': osm_id,
                'name': tags['name'],
                'lines': self._get_multiline_from_refs(refs),
            })
            return True
        return False

    def _import_relation(self, osm_id, tags, members):
        self._import_countrysubarea_from_relation(osm_id, tags, members)
        self._import_place_from_relation(osm_id, tags, members)
        self._import_placesubarea_from_relation(osm_id, tags, members)
        self._import_street_from_relation(osm_id, tags, members)

    def _import_way(self, osm_id, tags, refs):
        self._import_place_from_way(osm_id, tags, refs)
        self._import_street_from_way(osm_id, tags, refs)

    def _import_node(self, osm_id, tags, coords):
        self._import_place_from_node(osm_id, tags, coords)

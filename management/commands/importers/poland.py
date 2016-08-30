# -*- coding: utf-8 -*-
from django.contrib.gis.geos import Point
from .base import BaseImporter
from .. import CountrySubarea, Place, PlaceSubarea, Street


class PolandImporter(BaseImporter):
    def do_merge(self):
        # remove duplicates from country subareas, eg:
        # miasto Ełk / gmina miejska Ełk
        #
        # for country_subarea in self.country.subareas:

        for place in self.country.places.exclude(border=None).iterator():
            print(u"{}, starting with a merge...".format(place))
            self._merge_city_subareas(place)
            self._merge_streets(place)

    def _is_relation_to_cache(self, osm_id, tags, members):
        return any([
            self.__is_relation_street(osm_id, tags, members),
            self.__is_relation_city(osm_id, tags, members),
            self.__is_relation_citysubarea(osm_id, tags, members),
            self.__is_relation_countrysubarea(osm_id, tags, members),
        ])

    def _is_way_to_cache(self, osm_id, tags, refs):
        return any([
            self.__is_way_street(osm_id, tags, refs),
            self.__is_way_city(osm_id, tags, refs),
        ])

    def _is_node_to_cache(self, osm_id, tags, coords):
        return any([
            self.__is_node_city(osm_id, tags, coords)
        ])

    @classmethod
    def __is_relation_countrysubarea(cls, osm_id, tags, members):
        if 'admin_level' in tags and tags['admin_level'] in ['4', '6', '7'] and 'name' in tags and \
           'boundary' in tags and tags['boundary'] == 'administrative':
            return True
        return False

    @classmethod
    def __is_relation_city(cls, osm_id, tags, members):
        if 'admin_level' in tags and tags['admin_level'] in ['8'] and 'name' in tags and \
           'boundary' in tags and tags['boundary'] == 'administrative':
            return True
        return False

    @classmethod
    def __is_way_city(cls, osm_id, tags, refs):
        if len(refs) > 2 and cls.is_way_closed(refs) and 'place' in tags and\
                tags['place'] in set(('city', 'village', 'hamlet', 'town')) and 'name' in tags:
            return True
        return False

    @classmethod
    def __is_node_city(cls, osm_id, tags, coords):
        if 'place' in tags and tags['place'] in set(('city', 'village', 'hamlet', 'town')) and 'name' in tags:
            return True
        return False

    @classmethod
    def __is_relation_citysubarea(cls, osm_id, tags, members):
        if 'admin_level' in tags and tags['admin_level'] in ['9', '10', '11'] and 'name' in tags and \
           'boundary' in tags and tags['boundary'] == 'administrative':
            return True
        return False

    @classmethod
    def __is_relation_street(cls, osm_id, tags, members):
        if 'type' in tags and tags['type'] == 'associatedStreet' and 'name' in tags:
            return True
        return False

    @classmethod
    def __is_way_street(cls, osm_id, tags, refs):
        if len(refs) > 2 and 'highway' in tags and 'name' in tags:
            return True
        return False

    def _import_countrysubarea_from_relation(self, osm_id, tags, members):
        if self.__is_relation_countrysubarea(osm_id, tags, members) and self._are_all_members_cached(members):
            border = self._get_multipolygon_from_members(members)
            if border:
                print(tags['name'], osm_id)
                self.create_or_update(CountrySubarea, {
                    'relation_osm_id': osm_id,
                    'name': tags['name'],
                    'border': border,
                })
            return True
        return False

    def _import_city_from_relation(self, osm_id, tags, members):
        if self.__is_relation_city(osm_id, tags, members) and self._are_all_members_cached(members):
            border = self._get_multipolygon_from_members(members)
            if border:
                print(tags['name'], osm_id)
                self.create_or_update(Place, {
                    'relation_osm_id': osm_id,
                    'name': tags['name'],
                    'border': border,
                })
            return True
        return False

    def _import_city_from_way(self, osm_id, tags, refs):
        if self.__is_way_city(osm_id, tags, refs) and self._are_all_refs_cached(refs):
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

    def _import_city_from_node(self, osm_id, tags, coords):
        if self.__is_node_city(osm_id, tags, coords):
            print(tags['name'], osm_id)
            self.create_or_update(Place, {
                'node_osm_id': osm_id,
                'name': tags['name'],
                'center': Point(coords)
            })
            return True
        return False

    def _import_citysubarea_from_relation(self, osm_id, tags, members):
        if self.__is_relation_citysubarea(osm_id, tags, members) and self._are_all_members_cached(members):
            border = self._get_multipolygon_from_members(members)
            if border:
                print(tags['name'], osm_id)
                self.create_or_update(PlaceSubarea, {
                    'relation_osm_id': osm_id,
                    'name': tags['name'],
                    'border': border,
                })
            return True
        return False

    def _import_street_from_relation(self, osm_id, tags, members):
        if self.__is_relation_street(osm_id, tags, members) and self._are_all_members_cached(members):
            print(tags['name'], osm_id)
            self.create_or_update(Street, {
                'relation_osm_id': osm_id,
                'name': tags['name'],
                'lines': self._get_merged_multiline_from_members(members),
            })
            return True
        return False

    def _import_street_from_way(self, osm_id, tags, refs):
        if self.__is_way_street(osm_id, tags, refs) and self._are_all_refs_cached(refs):
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
        self._import_city_from_relation(osm_id, tags, members)
        self._import_citysubarea_from_relation(osm_id, tags, members)
        self._import_street_from_relation(osm_id, tags, members)

    def _import_way(self, osm_id, tags, refs):
        self._import_city_from_way(osm_id, tags, refs)
        self._import_street_from_way(osm_id, tags, refs)

    def _import_node(self, osm_id, tags, coords):
        self._import_city_from_node(osm_id, tags, coords)

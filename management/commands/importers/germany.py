# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from tqdm import tqdm

from .base import BaseImporter
from .. import Place


class GermanyImporter(BaseImporter):
    whitelist_tags = BaseImporter.whitelist_tags + ['de:place']

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
        is_relation_countrysubarea = 'admin_level' in tags and tags['admin_level'] in ['4', '5', '6', '7'] and \
            'name' in tags and 'boundary' in tags and tags['boundary'] == 'administrative'
        # ommit cities defined like: http://www.openstreetmap.org/relation/62649
        is_relation_countrysubarea = is_relation_countrysubarea and 'de:place' in tags and tags['de:place'] != 'city'

        if is_relation_countrysubarea:
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

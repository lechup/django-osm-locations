# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.contrib.gis.geos import MultiPolygon
from tqdm import tqdm

from .base import BaseImporter
from .. import CountrySubarea, Place


class PolandImporter(BaseImporter):
    def do_merge(self):
        # remove duplicates from country subareas
        # miasto Warszawa / gmina miejska Warszawa / miasto stołeczne Warszawa
        country_subareas = self.country.subareas.exclude(border=None).iterator()
        country_subarea_count = self.country.subareas.exclude(border=None).count()
        for country_subarea in tqdm(
                country_subareas, total=country_subarea_count, desc='Doing merge of CountrySubareas...', leave=False):
            self._merge_country_subareas(country_subarea)

        # remove duplicates from locations below places eg:
        # streets (one street can be saved as more than one line in OSM)
        places = self.country.places.exclude(border=None).iterator()
        places_count = self.country.places.exclude(border=None).count()
        for place in tqdm(places, total=places_count,
                desc='Doing merge of Places...', leave=False):
            self._merge_place_subareas(place)
            self._merge_streets(place)

    def do_update_country_border(self):
        country_border = MultiPolygon([])
        for country_subarea in CountrySubarea.objects.filter(name__icontains='województwo'):
            country_border = self._merge_multipolygon(country_border + country_subarea.border)
        self.country.border = country_border
        self.country.save()

    @classmethod
    def _merge_country_subareas(cls, country_subarea):
        is_country_subarea_place = Place.objects.filter(border=country_subarea.border).exists()
        if is_country_subarea_place:
            tqdm.write('{}, is also a Place - merged!'.format(country_subarea))
            country_subarea.mark_as_merged()

    @classmethod
    def _is_relation_countrysubarea(cls, osm_id, tags, members):
        if 'admin_level' in tags and tags['admin_level'] in ['4', '6', '7'] and 'name' in tags and \
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
        if 'admin_level' in tags and tags['admin_level'] in ['9', '10', '11'] and 'name' in tags and \
           'boundary' in tags and tags['boundary'] == 'administrative':
            return True
        return False

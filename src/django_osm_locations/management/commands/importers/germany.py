from .base import BaseImporter
from .. import Place


class GermanyImporter(BaseImporter):

    geofabrik_name = 'germany'
    country_name = 'Deutschland'
    country_slug = 'deutschland'
    iso2, iso3, un = 'DE', 'DEU', 276
    whitelist_tags = BaseImporter.whitelist_tags + ['de:place']

    @classmethod
    def _is_relation_countrysubarea(cls, osm_id, tags, members):
        is_relation_countrysubarea = 'admin_level' in tags and tags['admin_level'] in ['4', '5', '6', '7'] and \
            'name' in tags and 'boundary' in tags and tags['boundary'] == 'administrative'
        # Omit the cities at these levels -- http://www.openstreetmap.org/relation/62649
        # -- which _is_relation_place takes instead. Only those: this used to
        # require de:place to be present as well, and most Länder and Kreise
        # carry no de:place at all, so Germany would have come out with
        # hardly a region.
        is_relation_countrysubarea = is_relation_countrysubarea and tags.get('de:place') != 'city'
        if is_relation_countrysubarea:
            return True
        return False

    @classmethod
    def _is_relation_place(cls, osm_id, tags, members):
        is_relation_place = 'admin_level' in tags and 'name' in tags and 'boundary' in tags and \
            tags['boundary'] == 'administrative'
        # include default places
        if is_relation_place and tags['admin_level'] in ['8']:
            return True
        # include places defined like:
        # * http://www.openstreetmap.org/relation/62649
        # * http://www.openstreetmap.org/relation/62422
        elif is_relation_place and tags['admin_level'] in ['4', '5', '6', '7', '8'] and 'de:place' in tags \
                and tags['de:place'] == 'city':
            return True
        return False

    @classmethod
    def _is_way_place(cls, osm_id, tags, refs):
        if len(refs) > 2 and cls._is_way_closed(refs) and 'place' in tags and\
                tags['place'] in {'city', 'village', 'hamlet', 'town'} and 'name' in tags:
            return True
        return False

    @classmethod
    def _is_node_place(cls, osm_id, tags, coords):
        if 'place' in tags and tags['place'] in {'city', 'village', 'hamlet', 'town'} and 'name' in tags:
            return True
        return False

    @classmethod
    def _is_relation_placesubarea(cls, osm_id, tags, members):
        if 'admin_level' in tags and tags['admin_level'] in ['9', '10'] and 'name' in tags and \
           'boundary' in tags and tags['boundary'] == 'administrative':
            return True
        return False

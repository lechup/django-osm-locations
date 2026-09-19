"""
Shared shape for the Nordic and Low Countries importers.

They differ only in which admin_level means what, so the levels are class
attributes and the predicates are written once. Poland and Germany keep their
own classes: both have quirks these do not (Germany's de:place tag, Poland's
merging of a city with the county of the same name).
"""
from .base import BaseImporter

PLACE_NODE_TYPES = {'city', 'town', 'village', 'hamlet'}


class AdminLevelImporter(BaseImporter):
    #: Levels that are administrative divisions of the country.
    countrysubarea_levels = ()
    #: Levels that are municipalities -- the thing people call a place.
    place_levels = ()
    #: Levels below a municipality: boroughs, districts, neighbourhoods.
    placesubarea_levels = ()

    @classmethod
    def _is_admin(cls, tags, levels):
        return (
            tags.get('boundary') == 'administrative'
            and tags.get('admin_level') in levels
            and 'name' in tags
        )

    @classmethod
    def _is_relation_countrysubarea(cls, osm_id, tags, members):
        return cls._is_admin(tags, cls.countrysubarea_levels)

    @classmethod
    def _is_relation_place(cls, osm_id, tags, members):
        return cls._is_admin(tags, cls.place_levels)

    @classmethod
    def _is_relation_placesubarea(cls, osm_id, tags, members):
        return cls._is_admin(tags, cls.placesubarea_levels)

    @classmethod
    def _is_way_place(cls, osm_id, tags, refs):
        return (
            len(refs) > 2
            and cls._is_way_closed(refs)
            and tags.get('place') in PLACE_NODE_TYPES
            and 'name' in tags
        )

    @classmethod
    def _is_node_place(cls, osm_id, tags, coords):
        return tags.get('place') in PLACE_NODE_TYPES and 'name' in tags

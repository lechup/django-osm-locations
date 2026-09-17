from .base import BaseImporter


class IsleOfManImporter(BaseImporter):
    """
    Importer for the Isle of Man extract.

    Admin levels as they appear in the Geofabrik extract:

    ==  ==========================================================
    2   the island itself (and the United Kingdom, which the
        extract's bounding box also catches)
    4   England / Scotland -- likewise bleed-in from the bbox, and
        deliberately not treated as subareas of the island
    6   sheadings: Ayre, Garff, Glenfaba, Michael, Rushen, Middle
    8   parishes and towns: Douglas, Braddan, German, Marown, ...
    ==  ==========================================================

    There is no admin_level 9/10 in the extract, so place subareas come
    from nothing and stay empty.
    """

    @classmethod
    def _is_relation_countrysubarea(cls, osm_id, tags, members):
        return (
            tags.get('boundary') == 'administrative'
            and tags.get('admin_level') == '6'
            and 'name' in tags
        )

    @classmethod
    def _is_relation_place(cls, osm_id, tags, members):
        return (
            tags.get('boundary') == 'administrative'
            and tags.get('admin_level') == '8'
            and 'name' in tags
        )

    @classmethod
    def _is_relation_placesubarea(cls, osm_id, tags, members):
        return (
            tags.get('boundary') == 'administrative'
            and tags.get('admin_level') in ('9', '10')
            and 'name' in tags
        )

    @classmethod
    def _is_way_place(cls, osm_id, tags, refs):
        return (
            len(refs) > 2
            and cls._is_way_closed(refs)
            and tags.get('place') in {'city', 'village', 'hamlet', 'town'}
            and 'name' in tags
        )

    @classmethod
    def _is_node_place(cls, osm_id, tags, coords):
        return (
            tags.get('place') in {'city', 'village', 'hamlet', 'town'}
            and 'name' in tags
        )

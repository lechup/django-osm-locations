from ....utils.progress import progress

from .base import BaseImporter
from .. import Place


class PolandImporter(BaseImporter):
    """
    Poland.

    The border used to be assembled here by unioning every country subarea
    whose name contained "województwo" or "powiat". The base class now takes it
    from the extract's own admin_level=2 relation, which is both more accurate
    and does not depend on Polish words, so that override is gone.
    """

    geofabrik_name = 'poland'
    country_name = 'Polska'
    country_slug = 'polska'
    iso2, iso3, un = 'PL', 'POL', 616
    def before_merge(self):
        """
        Collapse a county that is really just the city inside it.

        Warszawa appears as miasto Warszawa, gmina miejska Warszawa and miasto
        stołeczne Warszawa; where a country subarea has exactly the border of a
        place, the subarea is the redundant one.

        This used to be an override of do_merge that called super() at the end.
        It is the same work, at a named point, without the chaining.
        """
        subareas = self.country.subareas.exclude(border=None)
        for country_subarea in progress(
                subareas.iterator(), total=subareas.count(),
                desc='Doing merge of CountrySubareas...', leave=False):
            self._merge_country_subareas(country_subarea)

    @classmethod
    def _merge_country_subareas(cls, country_subarea):
        is_country_subarea_place = Place.objects.filter(border=country_subarea.border).exists()
        if is_country_subarea_place:
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
        if 'admin_level' in tags and tags['admin_level'] in ['9', '10', '11'] and 'name' in tags and \
           'boundary' in tags and tags['boundary'] == 'administrative':
            return True
        return False

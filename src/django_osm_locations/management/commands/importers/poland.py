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

    #: What joins a name in two languages into one: "Wilamowice - Wymysoü".
    TWO_LANGUAGE_SEPARATORS = (' - ', ' – ', ' / ', '/')

    def before_import(self):
        """Name each place by its Polish name where its name is two languages at once."""
        for store in (self.relations, self.ways, self.nodes):
            for tags, _rest in store.values():
                self._take_polish_name(tags)

    @classmethod
    def _take_polish_name(cls, tags):
        """
        "Wilamowice - Wymysoü" becomes Wilamowice, its name:pl.

        The town's border carries both names, Polish and Wymysorys, in its
        name, and the gmina's and every register's name for it is
        Wilamowice. Under the double name the town was no Wilamowice at all:
        streets that name their town went to the village of Wilamowice
        31 km away. Only a name that is name:pl and something more after a
        separator is taken apart; a name:pl that says something else
        altogether -- "Suków Borki" for Borki -- is left for what it is.
        The other language is still found where it has a name tag of its
        own, as name:wym=Wymysoü does.

        Places and boundaries only. A street's name is what its addresses
        give as addr:street, read raw in a pass of their own, and a street
        renamed here would lose them.
        """
        if 'place' not in tags and 'boundary' not in tags:
            return
        polish, name = tags.get('name:pl'), tags.get('name')
        if not polish or not name or name == polish:
            return
        if any(name.startswith(polish + separator) for separator in cls.TWO_LANGUAGE_SEPARATORS):
            tags['name'] = polish

    def before_merge(self):
        """
        Collapse a county that is really just the city inside it.

        Warszawa appears as miasto Warszawa, gmina miejska Warszawa and miasto
        stołeczne Warszawa; where a country subarea has exactly the border of a
        place, the subarea is the redundant one.

        This used to be an override of do_merge that called super() at the end.
        It is the same work, at a named point, without the chaining.
        """
        self._give_seats_the_border_of_their_name(self.country.subareas.exclude(border=None))
        subareas = self.country.subareas.exclude(border=None)
        for country_subarea in progress(
                subareas.iterator(), total=subareas.count(),
                desc='Doing merge of CountrySubareas...', leave=False):
            self._merge_country_subareas(country_subarea)

    @classmethod
    def _give_seats_the_border_of_their_name(cls, country_subareas):
        """
        A town that is only a point takes the border of the unit named after
        it whose seat it is.

        Głogów and Trzcianka have no level 8 boundary, only a level 7 one
        under the town's own name: Głogów's is its gmina miejska, which is
        the town; Trzcianka's is the town's border tagged a level too high.
        Each came out twice and both halves wrong -- a town with no border
        and a "gmina" named like a town. The smallest such unit is taken, so
        a city-county that is also its gmina gets the gmina's border, not
        the county's with a bay in it. A unit named "gmina Trzcianka" is the
        gmina, and keeps its border to itself.

        Two guards against a unit that is more than its town. The town must
        lie inside: a seat can govern a ring around it, as Kraków does
        powiat krakowski. And no other place with a border may lie inside:
        a gmina holds its villages' sołectwa, a town holds none -- so a
        gmina ever tagged under its seat's bare name stays a gmina.
        """
        for subarea in country_subareas.exclude(seat_osm_node_id=None).order_by('area'):
            place = Place.objects.filter(
                name=subarea.name,
                node_osm_id=subarea.seat_osm_node_id,
                border=None,
                is_merged=False,
                center__within=subarea.border,
            ).first()
            if place is None:
                continue
            if Place.objects.filter(center__within=subarea.border, is_merged=False).exclude(border=None).exists():
                continue
            place.border = subarea.border
            place.save()

    @classmethod
    def _merge_country_subareas(cls, country_subarea):
        """
        A subarea is redundant when the place of the same name fills it.

        This used to ask for an identical border, which holds only inland.
        Gdansk's county boundary takes in the bay and the port -- 682 km2
        against the 396 km2 of the place -- so the city survived as four rows:
        the place, the level 6 city-county, the level 7 gmina, and only
        powiat gdanski legitimately beside them.

        Containment plus the name is what separates those. The name is doing
        real work: without it every county that happens to contain a town
        would match, and powiat gdanski -- which contains no place called
        powiat gdanski -- correctly does not.
        """
        is_country_subarea_place = Place.objects.filter(
            name=country_subarea.name,
            border__within=country_subarea.border,
        ).exists()
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

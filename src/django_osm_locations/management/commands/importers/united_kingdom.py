from .. import CountrySubarea, Place
from .nordic import AdminLevelImporter


class UnitedKingdomImporter(AdminLevelImporter):
    """
    The United Kingdom.

    Read from the extract. The UK itself is admin_level 2 with ISO3166-1 GB,
    while England, Scotland, Wales and Northern Ireland are admin_level 4 with
    subdivision codes GB-ENG, GB-SCT, GB-WLS and GB-NIR -- countries in the
    constitutional sense, subdivisions in the data, so they are country
    subareas. Metropolitan counties such as Greater London sit at 5 and
    counties such as Herefordshire at 6, which is why three levels are
    divisions here rather than one. The stored admin_level is what keeps them
    apart afterwards.

    Level 7 is Northern Ireland's eleven local government districts and
    nothing else: every level 7 relation in the extract carrying a GB code is
    one of them, the rest being County Donegal, County Leitrim and
    Cavan-Belturbet, which the boundary filter excludes. They are divisions in
    the same sense as an English county, so they belong with 4, 5 and 6.

    This used to put 7 among the places, on the grounds that leaving it out
    would drop Northern Ireland entirely. That was wrong: Northern Irish
    settlements come from ``place=*`` nodes, which AdminLevelImporter already
    imports, so Belfast, Lisburn and Derry were in the database regardless.
    What the old classification did instead was stand a district beside the
    city inside it -- Belfast City District, a boundary relation with no
    population, against Belfast, a node with 333,000 people and no border --
    with no merge rule able to connect them, because the names differ.

    Civil parishes at level 10 are numerous -- 21,710 in this extract.

    Geofabrik's "united-kingdom" includes Northern Ireland, unlike
    "great-britain". The extract also reaches into Ireland, France and the
    Isle of Man, which the country boundary filter excludes.
    """

    geofabrik_name = 'united-kingdom'
    country_name = 'United Kingdom'
    country_slug = 'united-kingdom'
    iso2, iso3, un = 'GB', 'GBR', 826

    countrysubarea_levels = ('4', '5', '6', '7')
    place_levels = ('8',)
    placesubarea_levels = ('9', '10')

    def after_import(self):
        """
        Retire places left behind by the level 7 reclassification.

        Nothing is deleted on import any more, so a relation that was a place
        under the old levels and is a country subarea under the new ones has a
        row in both tables. The country subarea is the live one; the place is
        a duplicate the search index should not carry, and marking it merged
        is how this codebase says so.

        Matching is by relation id, which is unique across OSM, rather than
        through ``self.country.subareas``: that filters on centres falling in
        the country's bounding box, and neither centre nor bbox is reliably
        populated at the point this hook runs.
        """
        stale = Place.objects.filter(
            is_merged=False,
            relation_osm_id__isnull=False,
            relation_osm_id__in=CountrySubarea.objects.exclude(
                relation_osm_id=None
            ).values('relation_osm_id'),
        )
        for place in stale:
            place.mark_as_merged()

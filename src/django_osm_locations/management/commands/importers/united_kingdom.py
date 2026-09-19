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

    Districts are at 8 in England, Scotland and Wales but at 7 in Northern
    Ireland, so both are places; leaving 7 out would drop Northern Ireland
    entirely.

    Civil parishes at level 10 are numerous -- 21,710 in this extract.

    Geofabrik's "united-kingdom" includes Northern Ireland, unlike
    "great-britain". The extract also reaches into Ireland, France and the
    Isle of Man, which the country boundary filter excludes.
    """

    geofabrik_name = 'united-kingdom'
    country_name = 'United Kingdom'
    country_slug = 'united-kingdom'
    iso2, iso3, un = 'GB', 'GBR', 826

    countrysubarea_levels = ('4', '5', '6')
    place_levels = ('7', '8')
    placesubarea_levels = ('9', '10')

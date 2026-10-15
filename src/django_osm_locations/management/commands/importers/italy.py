from .nordic import AdminLevelImporter


class ItalyImporter(AdminLevelImporter):
    """
    Italy.

    Read from the extract: twenty regioni at level 4, 109 province at 6 and
    7,894 comuni at 8. Below a comune: a handful of zone at 9, the municipi of
    Rome, Venice's islands and South Tyrol's Fraktionen at 10, and some
    quartieri at 11.

    Level 7 is left out: unioni di comuni and comunità montane, associations
    of municipalities rather than divisions of the country.

    The extract also holds San Marino and the Vatican, which are countries of
    their own, and slices of France, Switzerland, Austria and Slovenia; the
    country boundary filter excludes all of them.
    """

    geofabrik_name = 'italy'
    country_name = 'Italia'
    country_slug = 'italia'
    iso2, iso3, un = 'IT', 'ITA', 380

    countrysubarea_levels = ('4', '6')
    place_levels = ('8',)
    placesubarea_levels = ('9', '10', '11')

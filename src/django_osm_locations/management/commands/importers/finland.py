from .nordic import AdminLevelImporter


class FinlandImporter(AdminLevelImporter):
    """
    Finland.

    Maakunnat at level 4 and seutukunnat -- sub-regions -- at level 7, both
    treated as divisions of the country. Municipalities are at level 8, one
    deeper than in Denmark and Norway, with districts and neighbourhoods
    spreading across 9, 10 and 11.

    Level 3 holds Åland and Manner-Suomi, which are a constitutional split
    rather than a place anyone searches for, so they are left out.
    """

    geofabrik_name = 'finland'
    country_name = 'Suomi'
    country_slug = 'suomi'
    iso2, iso3, un = 'FI', 'FIN', 246

    countrysubarea_levels = ('4', '7')
    place_levels = ('8',)
    placesubarea_levels = ('9', '10', '11')

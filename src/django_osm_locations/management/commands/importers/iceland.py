from .nordic import AdminLevelImporter


class IcelandImporter(AdminLevelImporter):
    """
    Iceland.

    Read from the extract: eight regions at level 5 and 61 sveitarfélög at 6.
    A municipality is the place, as in Denmark and Norway: towns are place
    nodes. Below it Álftanes at 8, Reykjavík's districts at 9 and
    neighbourhoods -- Laugarnes, Vatnsmýri -- at 10.
    """

    geofabrik_name = 'iceland'
    country_name = 'Ísland'
    country_slug = 'island'
    iso2, iso3, un = 'IS', 'ISL', 352

    countrysubarea_levels = ('5',)
    place_levels = ('6',)
    placesubarea_levels = ('8', '9', '10')

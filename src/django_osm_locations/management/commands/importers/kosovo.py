from .nordic import AdminLevelImporter


class KosovoImporter(AdminLevelImporter):
    """
    Kosovo.

    Read from the extract: seven districts at level 5 and 38 komuna at 6,
    then 165 settlements at 8; their parts are at 9 and 10.

    Kosovo has no ISO 3166 numeric code -- XK is a code the EU and others
    agreed on, not one ISO assigned -- so 900, the first of the numbers ISO
    leaves for user assignment, stands in. Nothing outside this project reads
    it.

    The extract reaches into Serbia, Montenegro, Albania and North Macedonia,
    which the country boundary filter excludes.
    """

    geofabrik_name = 'kosovo'
    country_name = 'Kosova / Kosovo'
    country_slug = 'kosova-kosovo'
    iso2, iso3, un = 'XK', 'XKK', 900

    countrysubarea_levels = ('5', '6')
    place_levels = ('8',)
    placesubarea_levels = ('9', '10')

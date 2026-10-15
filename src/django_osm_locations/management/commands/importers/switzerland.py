from .nordic import AdminLevelImporter


class SwitzerlandImporter(AdminLevelImporter):
    """
    Switzerland.

    Read from the extract: cantons at level 4, Bern's administrative regions
    at 5, districts at 6 and municipalities -- Gemeinden, communes, comuni --
    at 8. Levels 9 and 10 hold former municipalities merged into larger ones
    and city quarters, which are what a subarea is for.

    The country's name is OSM's own, in all four national languages, rather
    than one of them: Schweiz/Suisse/Svizzera/Svizra. Its slug follows from
    that, since slugify splits on the slash.

    The extract reaches well into France, Germany, Italy, Austria and all of
    Liechtenstein -- 14 level-2 relations, border sections included -- which
    the country boundary filter excludes.
    """

    geofabrik_name = 'switzerland'
    country_name = 'Schweiz/Suisse/Svizzera/Svizra'
    country_slug = 'schweiz-suisse-svizzera-svizra'
    iso2, iso3, un = 'CH', 'CHE', 756

    countrysubarea_levels = ('4', '5', '6')
    place_levels = ('8',)
    placesubarea_levels = ('9', '10')

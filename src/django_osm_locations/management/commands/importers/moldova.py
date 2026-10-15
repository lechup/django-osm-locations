from .nordic import AdminLevelImporter


class MoldovaImporter(AdminLevelImporter):
    """
    Moldova, with Transnistria.

    The whole country as OSM's border for Moldova draws it. Transnistria is
    drawn at level 3 and kept, with its raiony at 5 alongside Gagauzia's.

    Read from the extract: 37 raioane, municipalities and Gagauzia at level 4
    and Chișinău's five sectors at 7. Places are the 982 communes and towns at
    8. Level 9 holds the villages within a commune, and state enterprises'
    land -- Î.P. „Berpalox” -- tagged the same way; both are below a place.

    The extract reaches into Romania and Ukraine, which the country boundary
    filter excludes.
    """

    geofabrik_name = 'moldova'
    country_name = 'Moldova'
    country_slug = 'moldova'
    iso2, iso3, un = 'MD', 'MDA', 498

    countrysubarea_levels = ('3', '4', '5')
    place_levels = ('8',)
    placesubarea_levels = ('7', '9')

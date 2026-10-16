from .nordic import AdminLevelImporter


class MonacoImporter(AdminLevelImporter):
    """
    Monaco.

    Read from the extract: Monaco itself at level 8, the one place, and its
    nine quartiers -- Monte-Carlo, La Condamine, Fontvieille -- at 10.
    """

    geofabrik_name = 'monaco'
    country_name = 'Monaco'
    country_slug = 'monaco'
    iso2, iso3, un = 'MC', 'MCO', 492

    countrysubarea_levels = ()
    place_levels = ('8',)
    placesubarea_levels = ('10',)

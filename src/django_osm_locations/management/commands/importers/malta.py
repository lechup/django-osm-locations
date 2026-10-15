from .nordic import AdminLevelImporter


class MaltaImporter(AdminLevelImporter):
    """
    Malta.

    Read from the extract: six regions at level 7 and 68 localities at 8,
    with Fleur-de-Lys at 10.
    """

    geofabrik_name = 'malta'
    country_name = 'Malta'
    country_slug = 'malta'
    iso2, iso3, un = 'MT', 'MLT', 470

    countrysubarea_levels = ('7',)
    place_levels = ('8',)
    placesubarea_levels = ('10',)

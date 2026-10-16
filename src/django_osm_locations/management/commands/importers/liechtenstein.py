from .nordic import AdminLevelImporter


class LiechtensteinImporter(AdminLevelImporter):
    """
    Liechtenstein.

    Read from the extract: Oberland and Unterland at level 6 and the eleven
    Gemeinden at 8.
    """

    geofabrik_name = 'liechtenstein'
    country_name = 'Liechtenstein'
    country_slug = 'liechtenstein'
    iso2, iso3, un = 'LI', 'LIE', 438

    countrysubarea_levels = ('6',)
    place_levels = ('8',)
    placesubarea_levels = ()

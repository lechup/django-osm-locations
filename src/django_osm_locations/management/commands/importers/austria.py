from .nordic import AdminLevelImporter


class AustriaImporter(AdminLevelImporter):
    """
    Austria.

    Read from the extract: the Länder at level 4, Bezirke at 6 and Gemeinden at
    8, with the districts of the larger cities -- Graz's Innere Stadt, Geidorf,
    Liebenau -- at 9.

    Level 10 is left out although it is the largest tier by far: 5,196
    relations, nearly all Katastralgemeinden, the cadastral units of the land
    register. They are real boundaries and nobody searches for one. Level 7 is
    the handful of Politische Expositur, branch offices of a Bezirk.

    The extract reaches into all eight neighbours, which the country boundary
    filter excludes.
    """

    geofabrik_name = 'austria'
    country_name = 'Österreich'
    country_slug = 'osterreich'
    iso2, iso3, un = 'AT', 'AUT', 40

    countrysubarea_levels = ('4', '6')
    place_levels = ('8',)
    placesubarea_levels = ('9',)

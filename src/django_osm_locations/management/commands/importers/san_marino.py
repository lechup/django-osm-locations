from .nordic import AdminLevelImporter


class SanMarinoImporter(AdminLevelImporter):
    """
    San Marino.

    San Marino has no Geofabrik extract of its own; it is read from Italy's,
    and its border keeps Italy out.

    Read from the extract: the nine castelli at level 8, and Dogana and
    Falciano at 9.
    """

    geofabrik_name = 'italy'
    # its corner of Italy; reading the whole extract indexes every Italian node
    extract_bbox = (12.39, 43.88, 12.53, 44.00)
    name = 'san-marino'
    country_name = 'San Marino'
    country_slug = 'san-marino'
    iso2, iso3, un = 'SM', 'SMR', 674

    countrysubarea_levels = ()
    place_levels = ('8',)
    placesubarea_levels = ('9',)

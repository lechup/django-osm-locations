from .nordic import AdminLevelImporter


class LuxembourgImporter(AdminLevelImporter):
    """
    Luxembourg.

    Read from the extract: twelve cantons at level 6, 100 communes at 8, and
    the localities and quarters of a commune -- Gasperich, Cessange -- at 9,
    with Belval at 10.

    The extract reaches into Belgium, France and Germany, which the country boundary filter
    excludes.
    """

    geofabrik_name = 'luxembourg'
    country_name = 'Lëtzebuerg'
    country_slug = 'letzebuerg'
    iso2, iso3, un = 'LU', 'LUX', 442

    countrysubarea_levels = ('6',)
    place_levels = ('8',)
    placesubarea_levels = ('9', '10')

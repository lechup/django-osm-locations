from .nordic import AdminLevelImporter


class SerbiaImporter(AdminLevelImporter):
    """
    Serbia.

    Read from the extract: Vojvodina and Central Serbia at level 4, 25
    upravni okruzi at 6, the 28 cities at 7 and 146 opštine at 8 -- all of
    them divisions. The settlements, 4,721 naselja, are at 9: Serbia draws its
    towns and villages, which is where a place belongs. Mesne zajednice, the
    local communities, are at 10.

    Kosovo is a level-2 boundary of its own, outside Serbia's.

    The extract reaches into all eight neighbours, which the country boundary
    filter excludes.
    """

    geofabrik_name = 'serbia'
    country_name = 'Србија'
    country_slug = 'srbija'
    iso2, iso3, un = 'RS', 'SRB', 688

    countrysubarea_levels = ('4', '6', '7', '8')
    place_levels = ('9',)
    placesubarea_levels = ('10',)

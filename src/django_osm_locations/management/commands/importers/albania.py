from .nordic import AdminLevelImporter


class AlbaniaImporter(AdminLevelImporter):
    """
    Albania.

    Read from the extract: twelve qarqe at level 6 and 61 bashki at 7; then
    371 njësi administrative at 8 -- the former communes, a town or a group
    of villages each, named for it. Lagje, the city quarters, are at 9 and
    10.

    The extract reaches into Montenegro, Kosovo, North Macedonia and Greece, which the country boundary filter
    excludes.
    """

    geofabrik_name = 'albania'
    country_name = 'Shqipëria'
    country_slug = 'shqiperia'
    iso2, iso3, un = 'AL', 'ALB', 8

    countrysubarea_levels = ('6', '7')
    place_levels = ('8',)
    placesubarea_levels = ('9', '10')

from .nordic import AdminLevelImporter


class GreeceImporter(AdminLevelImporter):
    """
    Greece.

    Read from the extract: Mount Athos, self-governing, at level 3; the seven
    decentralised administrations at 4, thirteen regions at 5 and 74 regional
    units at 6; then 332 dimoi -- the municipalities -- at 7. Below a dimos:
    the 464 municipal units at 8, the former municipalities merged in 2011,
    and the communities at 9 and 10.

    The extract reaches into Albania, North Macedonia, Bulgaria and Turkey,
    which the country boundary filter excludes.
    """

    geofabrik_name = 'greece'
    country_name = 'Ελλάς'
    country_slug = 'ellas'
    iso2, iso3, un = 'GR', 'GRC', 300

    countrysubarea_levels = ('3', '4', '5', '6')
    place_levels = ('7',)
    placesubarea_levels = ('8', '9', '10')

from .nordic import AdminLevelImporter


class HungaryImporter(AdminLevelImporter):
    """
    Hungary.

    Read from the extract: nineteen megyék and Budapest at level 6 and 174
    járások at 7, then 3,155 municipalities at 8. Below them Budapest's
    kerületek at 9, and parts of towns -- Soroksár, telepek, tanyák -- at 10
    and 11. No level 4 or 5 is drawn.

    The extract reaches into all seven neighbours, which the country boundary
    filter excludes.
    """

    geofabrik_name = 'hungary'
    country_name = 'Magyarország'
    country_slug = 'magyarorszag'
    iso2, iso3, un = 'HU', 'HUN', 348

    countrysubarea_levels = ('6', '7')
    place_levels = ('8',)
    placesubarea_levels = ('9', '10', '11')

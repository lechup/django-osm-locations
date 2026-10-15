from .nordic import AdminLevelImporter


class PortugalImporter(AdminLevelImporter):
    """
    Portugal.

    Read from the extract: the Azores and Madeira at level 4, eighteen
    distritos at 6, 308 concelhos -- the municipalities -- at 7 and 3,258
    freguesias at 8.

    The extract reaches into Spain, which the country boundary filter
    excludes.
    """

    geofabrik_name = 'portugal'
    country_name = 'Portugal'
    country_slug = 'portugal'
    iso2, iso3, un = 'PT', 'PRT', 620

    countrysubarea_levels = ('4', '6')
    place_levels = ('7',)
    placesubarea_levels = ('8',)

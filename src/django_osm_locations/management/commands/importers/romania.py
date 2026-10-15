from .nordic import AdminLevelImporter


class RomaniaImporter(AdminLevelImporter):
    """
    Romania.

    Read from the extract: 41 județe and Bucharest at level 4, then 3,179
    municipalities, towns and communes at 8, and Bucharest's six sectors at 9.
    The villages a commune is made of have no boundary; they are place nodes.

    Level 7 is seven metropolitan areas, associations rather than divisions.
    Level 10 is three neighbourhoods of Cluj.

    The extract reaches into Hungary, Serbia, Bulgaria, Ukraine and Moldova,
    which the country boundary filter excludes.
    """

    geofabrik_name = 'romania'
    country_name = 'România'
    country_slug = 'romania'
    iso2, iso3, un = 'RO', 'ROU', 642

    countrysubarea_levels = ('4',)
    place_levels = ('8',)
    placesubarea_levels = ('9', '10')

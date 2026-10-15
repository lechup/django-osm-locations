from .nordic import AdminLevelImporter


class CzechiaImporter(AdminLevelImporter):
    """
    Czechia.

    Read from the extract: fourteen kraje at level 4 and 86 okresy at 5, then
    6,257 obce at 8; below them Prague's and Brno's city districts at 9 and the
    části obce, the villages a municipality is made of, at 10.

    Levels 6 and 7 are left out: "SO ORP" and "SO POÚ", the areas served by a
    municipality with extended powers. They are administrative and nobody
    lives in one by that name.

    The extract reaches into Germany, Austria, Poland and Slovakia, which the
    country boundary filter excludes.
    """

    geofabrik_name = 'czech-republic'
    country_name = 'Česko'
    country_slug = 'cesko'
    iso2, iso3, un = 'CZ', 'CZE', 203

    countrysubarea_levels = ('4', '5')
    place_levels = ('8',)
    placesubarea_levels = ('9', '10')

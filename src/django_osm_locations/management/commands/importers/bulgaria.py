from .nordic import AdminLevelImporter


class BulgariaImporter(AdminLevelImporter):
    """
    Bulgaria.

    Read from the extract: 28 oblasti at level 4 and 265 obshtini at 5, with
    Sofia's raioni -- Pancharevo, Kremikovtsi -- at 6. Those districts reach
    past the city into the villages around it, so they are divisions rather
    than parts of Sofia. Then 2,001 settlements at 8, and quarters -- zh.k.
    Lyulin, makhali -- at 9 and 10.

    The extract reaches into Romania, Serbia, North Macedonia, Greece and
    Turkey, which the country boundary filter excludes.
    """

    geofabrik_name = 'bulgaria'
    country_name = 'България'
    country_slug = 'blgariya'
    iso2, iso3, un = 'BG', 'BGR', 100

    countrysubarea_levels = ('4', '5', '6')
    place_levels = ('8',)
    placesubarea_levels = ('9', '10')

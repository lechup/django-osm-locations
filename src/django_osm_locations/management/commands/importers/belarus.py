from .nordic import AdminLevelImporter


class BelarusImporter(AdminLevelImporter):
    """
    Belarus.

    Read from the extract: six voblasci and Minsk at level 4, 128 raiony at
    6, and at 8 the sel'savety -- rural councils, territory rather than
    settlements -- alongside the councils of small towns. The settlements
    themselves, 22,341 of them, are at 10. Minsk's raiony are at 9.

    Minsk is a division, as OSM draws it, so a Minsk address takes its
    place from the nearest place node: Minsk itself.

    The extract reaches into Poland, Lithuania, Latvia, Russia and Ukraine, which the country boundary filter
    excludes.
    """

    geofabrik_name = 'belarus'
    country_name = 'Беларусь'
    country_slug = 'belarus'
    iso2, iso3, un = 'BY', 'BLR', 112

    countrysubarea_levels = ('4', '6', '8')
    place_levels = ('10',)
    placesubarea_levels = ('9',)

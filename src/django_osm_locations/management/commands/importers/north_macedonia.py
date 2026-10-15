from .nordic import AdminLevelImporter


class NorthMacedoniaImporter(AdminLevelImporter):
    """
    North Macedonia.

    Read from the extract: the City of Skopje at level 6 and 80 opštini at
    7, then 1,748 settlements at 8. Skopje's own opštini -- Karpoš, Gazi
    Baba -- are at 9, inside the city.

    The extract reaches into Serbia, Kosovo, Albania, Greece and Bulgaria, which the country boundary filter
    excludes.
    """

    geofabrik_name = 'macedonia'
    country_name = 'Северна Македонија'
    country_slug = 'severna-makedonija'
    iso2, iso3, un = 'MK', 'MKD', 807

    countrysubarea_levels = ('6', '7')
    place_levels = ('8',)
    placesubarea_levels = ('9',)

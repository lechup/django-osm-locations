from .nordic import AdminLevelImporter


class MontenegroImporter(AdminLevelImporter):
    """
    Montenegro.

    Read from the extract: 25 opštine at level 6, 233 settlements at 8 and
    their parts at 9.

    The extract reaches into Croatia, Bosnia and Herzegovina, Serbia, Kosovo and Albania, which the country boundary filter
    excludes.
    """

    geofabrik_name = 'montenegro'
    country_name = 'Crna Gora / Црна Гора'
    country_slug = 'crna-gora-tsrna-gora'
    iso2, iso3, un = 'ME', 'MNE', 499

    countrysubarea_levels = ('6',)
    place_levels = ('8',)
    placesubarea_levels = ('9',)

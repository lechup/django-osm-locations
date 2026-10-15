from .nordic import AdminLevelImporter


class LithuaniaImporter(AdminLevelImporter):
    """
    Lithuania.

    Read from the extract: ten apskritys at level 4, 60 savivaldybės at 5 and
    506 seniūnijos at 6; then 4,534 settlements at 8, Vilnius and Kaunas
    among them, and Vilnius's own seniūnijos -- Antakalnis, Žvėrynas -- at
    10.

    The extract reaches into Latvia, Belarus, Poland and Russia, which the country boundary filter
    excludes.
    """

    geofabrik_name = 'lithuania'
    country_name = 'Lietuva'
    country_slug = 'lietuva'
    iso2, iso3, un = 'LT', 'LTU', 440

    countrysubarea_levels = ('4', '5', '6')
    place_levels = ('8',)
    placesubarea_levels = ('10',)

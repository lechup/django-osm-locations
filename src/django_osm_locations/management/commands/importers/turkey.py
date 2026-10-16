from .nordic import AdminLevelImporter


class TurkeyImporter(AdminLevelImporter):
    """
    Turkey.

    Read from the extract: 81 iller at level 4 and 974 ilçeler at 6. An ilçe
    is the place: Kadıköy, Seyitgazi -- the name people give, and in a
    province centre the city itself. Below it 83 bucaklar and beldeler at 7,
    and the 13,809 mahalleler and köyler at 8, with a few more at 9 and 10.

    Level 3 is the seven geographical regions, which are statistical and left
    out, as is a lone boundary tagged level 88.

    The extract reaches into Greece, Bulgaria, Georgia, Armenia, Iran, Iraq
    and Syria, which the country boundary filter excludes.
    """

    geofabrik_name = 'turkey'
    country_name = 'Türkiye'
    country_slug = 'turkiye'
    iso2, iso3, un = 'TR', 'TUR', 792

    countrysubarea_levels = ('4',)
    place_levels = ('6',)
    placesubarea_levels = ('7', '8', '9', '10')

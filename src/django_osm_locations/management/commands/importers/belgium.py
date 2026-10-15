from .nordic import AdminLevelImporter


class BelgiumImporter(AdminLevelImporter):
    """
    Belgium.

    Read from the extract: the three regions at level 4, ten provinces at 6
    and 43 arrondissements at 7, then 565 gemeenten/communes at 8 and their
    deelgemeenten/sections -- the municipalities merged in 1977, Waarloos,
    Vremde -- at 9.

    Brussels is a region with no province above its communes. Levels 10 and 11
    are a scattering of parishes and hamlets, 214 between them.

    The extract reaches into the Netherlands, Germany, Luxembourg and France,
    which the country boundary filter excludes.
    """

    geofabrik_name = 'belgium'
    country_name = 'België / Belgique / Belgien'
    country_slug = 'belgie-belgique-belgien'
    iso2, iso3, un = 'BE', 'BEL', 56

    countrysubarea_levels = ('4', '6', '7')
    place_levels = ('8',)
    placesubarea_levels = ('9',)

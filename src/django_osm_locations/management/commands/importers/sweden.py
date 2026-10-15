from .nordic import AdminLevelImporter


class SwedenImporter(AdminLevelImporter):
    """
    Sweden.

    Read from the extract: län at level 4 and kommuner at level 7, with
    stadsdelar -- city districts and neighbourhoods -- at 9 and 10 (529 and
    537 relations, neighbours included). A kommun is the municipality, like a
    Norwegian kommune, and often holds several towns; the towns themselves come
    in as place nodes.

    Level 8 carries distrikt, the parish-based registration districts, and is
    left out: 98 relations are mapped of roughly 2,500 that exist, so it would
    give a few areas a layer the rest of the country lacks. Level 6 is two
    Sámi villages. The extract reaches into Norway, Finland and Denmark, which
    the country boundary filter excludes.
    """

    geofabrik_name = 'sweden'
    country_name = 'Sverige'
    country_slug = 'sverige'
    iso2, iso3, un = 'SE', 'SWE', 752

    countrysubarea_levels = ('4',)
    place_levels = ('7',)
    placesubarea_levels = ('9', '10')

from .nordic import AdminLevelImporter


class IrelandImporter(AdminLevelImporter):
    """
    Ireland.

    Read from the extract: four provinces at level 5, 26 counties at 6, the
    20 local authorities -- county and city councils -- at 7 and 98 municipal
    districts at 8. All of them are divisions: no level is a town. Irish towns
    and villages have no boundary in OSM, so every place is a place node.

    Below them, 51,412 townlands at level 10: the old land units rural
    addresses are still written with. With no place border to contain them a
    townland has no place of its own, only its county. Level 9 is the 3,620
    electoral divisions, which are for counting votes and are left out.

    The extract carries Northern Ireland too, already in the United Kingdom's
    pack; the country boundary filter excludes it.
    """

    geofabrik_name = 'ireland-and-northern-ireland'
    country_name = 'Éire / Ireland'
    country_slug = 'eire-ireland'
    iso2, iso3, un = 'IE', 'IRL', 372

    countrysubarea_levels = ('5', '6', '7', '8')
    place_levels = ()
    placesubarea_levels = ('10',)

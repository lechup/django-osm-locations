from .nordic import AdminLevelImporter


class AndorraImporter(AdminLevelImporter):
    """
    Andorra.

    Read from the extract: the seven parròquies at level 7 and nothing else.
    A parish is the place; its villages are place nodes.
    """

    geofabrik_name = 'andorra'
    country_name = 'Andorra'
    country_slug = 'andorra'
    iso2, iso3, un = 'AD', 'AND', 20

    countrysubarea_levels = ()
    place_levels = ('7',)
    placesubarea_levels = ()

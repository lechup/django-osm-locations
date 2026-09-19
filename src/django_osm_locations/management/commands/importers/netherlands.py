from .nordic import AdminLevelImporter


class NetherlandsImporter(AdminLevelImporter):
    """
    The Netherlands.

    Twelve provinces at level 4 and 432 gemeenten at level 8, with wijken and
    buurten below at 9 and 10. Level 5 and 6 in this extract are German
    Regierungsbezirke and Belgian provinces rather than anything Dutch.

    The unnamed level in the extract is the water boards -- Hoogheemraadschap
    van Delfland and the rest -- which are administrative but not places.
    """

    geofabrik_name = 'netherlands'
    country_name = 'Nederland'
    country_slug = 'nederland'
    iso2, iso3, un = 'NL', 'NLD', 528

    countrysubarea_levels = ('4',)
    place_levels = ('8',)
    placesubarea_levels = ('9', '10')

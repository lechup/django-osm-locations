from .nordic import AdminLevelImporter


class SpainImporter(AdminLevelImporter):
    """
    Spain, with the Canary Islands.

    Geofabrik files the Canaries under Africa, so the Spanish extract alone
    leaves out 2.2 million people and Spain's border cannot close around them.
    The two extracts are merged before the import reads them. Eight of the
    border's 640 ways are still in neither: the islets off Morocco.

    Read from the merged extract: seventeen comunidades autónomas plus Ceuta
    and Melilla at level 4, fifty provincias at 6 and 8,262 municipios at 8.
    Below a municipio: entidades locales, parroquias and districts at 9,
    barrios at 10, and a few urbanizaciones at 11.

    Level 7 is left out: comarcas, official in some regions and not in others.

    The extracts reach into Portugal, France, Andorra, Gibraltar and Morocco,
    which the country boundary filter excludes.
    """

    geofabrik_name = 'spain'
    extra_extracts = ('africa/canary-islands',)
    country_name = 'España'
    country_slug = 'espana'
    iso2, iso3, un = 'ES', 'ESP', 724

    countrysubarea_levels = ('4', '6')
    place_levels = ('8',)
    placesubarea_levels = ('9', '10', '11')

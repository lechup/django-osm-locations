from .nordic import AdminLevelImporter


class LatviaImporter(AdminLevelImporter):
    """
    Latvia.

    Read from the extract: 42 novadi and state cities at level 5, Riga among
    them; 75 towns at 7; and 511 pagasti -- rural parishes, territory rather
    than settlements -- at 8, so they are divisions. Villages are place
    nodes. Riga's apkaimes and the ciemi with a boundary are at 9, a few more
    at 10.

    A state city is a division here, as OSM draws it, so a Riga address
    takes its place from the nearest place node: Rīga itself.

    The extract reaches into Estonia, Lithuania, Belarus and Russia, which the country boundary filter
    excludes.
    """

    geofabrik_name = 'latvia'
    country_name = 'Latvija'
    country_slug = 'latvija'
    iso2, iso3, un = 'LV', 'LVA', 428

    countrysubarea_levels = ('5', '8')
    place_levels = ('7',)
    placesubarea_levels = ('9', '10')

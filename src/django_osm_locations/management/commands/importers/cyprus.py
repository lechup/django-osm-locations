from .nordic import AdminLevelImporter


class CyprusImporter(AdminLevelImporter):
    """
    Cyprus.

    The whole island, as OSM's border for Cyprus draws it.

    Read from the extract: the Republic of Cyprus and Northern Cyprus at
    level 3, the districts -- eparchies and ilçeler -- at 5 and the community
    complexes and municipalities at 6. Places are at 7, the northern
    belediyeler, and at 8, the 673 communities; a village inside a northern
    municipality is the smaller place, and an address takes the smallest.
    Quarters and mahalleler are at 9.

    The British Sovereign Base Areas, Akrotiri and Dhekelia, are the United
    Kingdom's and outside the border.
    """

    geofabrik_name = 'cyprus'
    country_name = 'Κύπρος - Kıbrıs'
    country_slug = 'kypros-kibris'
    iso2, iso3, un = 'CY', 'CYP', 196

    countrysubarea_levels = ('3', '5', '6')
    place_levels = ('7', '8')
    placesubarea_levels = ('9',)

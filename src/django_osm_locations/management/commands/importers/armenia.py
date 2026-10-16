from .nordic import AdminLevelImporter


class ArmeniaImporter(AdminLevelImporter):
    """
    Armenia.

    Geofabrik files Armenia under Asia.

    Read from the extract: ten marzer and Yerevan at level 4 and 80
    consolidated communities at 5. Settlements are drawn at three levels --
    fifteen towns at 6, 156 villages at 7 and six more towns at 8 -- and all
    of them are places. Level 10 is seven villages more; the 37 boundaries
    with no level are left out.

    Yerevan is a division, as OSM draws it, so a Yerevan address takes its
    place from the nearest place node: Yerevan itself.
    """

    geofabrik_name = 'armenia'
    geofabrik_path = 'asia/'
    country_name = 'Հայաստան'
    country_slug = 'hayastan'
    iso2, iso3, un = 'AM', 'ARM', 51

    countrysubarea_levels = ('4', '5')
    place_levels = ('6', '7', '8')
    placesubarea_levels = ('10',)

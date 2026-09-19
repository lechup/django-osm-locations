from .nordic import AdminLevelImporter


class NorwayImporter(AdminLevelImporter):
    """
    Norway.

    Fylker at level 4 and 389 kommuner at level 7, with city boroughs -- Bergen
    and Oslo's bydeler -- at level 9. The extract reaches into Sweden, Finland
    and Russia, which the country boundary filter excludes.

    Norway tags a great many farms and isolated dwellings as places (105,989 of
    them here), which is why only city/town/village/hamlet are imported.
    """

    countrysubarea_levels = ('4',)
    place_levels = ('7',)
    placesubarea_levels = ('9',)

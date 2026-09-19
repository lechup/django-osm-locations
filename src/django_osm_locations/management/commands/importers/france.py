from .nordic import AdminLevelImporter


class FranceImporter(AdminLevelImporter):
    """
    France.

    Read from the extract: 13 régions at level 4 and 101 départements at level
    6, then 35,099 communes at level 8 -- the largest single tier of any
    country here. Below that come communes déléguées and the arrondissements of
    Paris, Lyon and Marseille at 9, and quartiers at 10 and 11.

    Levels 5 and 7 are almost entirely German in this extract -- Regierungs-
    bezirke and Verbandsgemeinden along the Rhine -- so neither is imported.
    French arrondissements are not consistently mapped as administrative
    boundaries and do not appear as a tier of their own.

    Geofabrik's "france" is metropolitan France and Corsica; the overseas
    départements are not in it, so nothing here reaches the Caribbean or the
    Indian Ocean.
    """

    geofabrik_name = 'france'
    country_name = 'France'
    country_slug = 'france'
    iso2, iso3, un = 'FR', 'FRA', 250

    countrysubarea_levels = ('4', '6')
    place_levels = ('8',)
    placesubarea_levels = ('9', '10', '11')

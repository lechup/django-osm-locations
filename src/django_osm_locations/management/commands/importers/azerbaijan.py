from .nordic import AdminLevelImporter


class AzerbaijanImporter(AdminLevelImporter):
    """
    Azerbaijan.

    Geofabrik files Azerbaijan under Asia.

    Sparsely drawn. Read from the extract: Nakhchivan at level 4 and 78 rayons
    at 6, and Baku's rayons -- Yasamal, Sabunçu, Xəzər -- at 7 and 8, all of
    them divisions. No settlement has a boundary: every place is a place
    node. Level 9 is three electoral districts, which are left out.
    """

    geofabrik_name = 'azerbaijan'
    geofabrik_path = 'asia/'
    country_name = 'Azərbaycan'
    country_slug = 'azerbaycan'
    iso2, iso3, un = 'AZ', 'AZE', 31

    countrysubarea_levels = ('4', '6', '7', '8')
    place_levels = ()
    placesubarea_levels = ()

from .nordic import AdminLevelImporter


class SlovakiaImporter(AdminLevelImporter):
    """
    Slovakia.

    Read from the extract: eight kraje at level 4 and 79 okresy at 6, then
    2,888 obce at 8. Bratislava and Košice are different: each city is drawn
    at level 5, and its mestské časti -- Podunajské Biskupce, Šaca -- are
    municipalities in law, at 9. So level 5 is a place too, holding the city
    parts beneath it. Below an obec: Prešov's parts at 10 and local parts at 11.

    Level 12 is 26 named woods and estates.

    The extract reaches into all five neighbours, which the country boundary
    filter excludes.
    """

    geofabrik_name = 'slovakia'
    country_name = 'Slovensko'
    country_slug = 'slovensko'
    iso2, iso3, un = 'SK', 'SVK', 703

    countrysubarea_levels = ('4', '6')
    place_levels = ('5', '8')
    placesubarea_levels = ('9', '10', '11')

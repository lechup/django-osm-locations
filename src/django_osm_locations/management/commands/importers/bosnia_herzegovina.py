from .nordic import AdminLevelImporter


class BosniaHerzegovinaImporter(AdminLevelImporter):
    """
    Bosnia and Herzegovina.

    Read from the extract: the Federation, Republika Srpska and Brčko
    District at level 4, the Federation's ten cantons at 5, the cities at 6
    and the municipalities at 7 -- all divisions. The settlements, 3,604
    naselja, are at 9. Mjesne zajednice, local communities, are at 8, 10 and
    11; in a city they are its quarters, Bistrik and Logavina in Sarajevo.

    The extract reaches into Croatia, Serbia and Montenegro, which the country boundary filter
    excludes.
    """

    geofabrik_name = 'bosnia-herzegovina'
    country_name = 'Bosna i Hercegovina / Босна и Херцеговина'
    country_slug = 'bosna-i-hercegovina-bosna-i-khertsegovina'
    iso2, iso3, un = 'BA', 'BIH', 70

    countrysubarea_levels = ('4', '5', '6', '7')
    place_levels = ('9',)
    placesubarea_levels = ('8', '10', '11')

from .nordic import AdminLevelImporter


class GeorgiaImporter(AdminLevelImporter):
    """
    Georgia.

    The whole country as OSM's border for Georgia draws it. Abkhazia and South
    Ossetia are drawn at level 3 and kept as divisions, as Transnistria is in
    Moldova.

    Read from the extract: twelve regions and autonomous republics at level 4
    and 77 municipalities at 6, then 69 towns and cities at 8. Villages are
    place nodes. The districts of Tbilisi and Batumi -- Saburtalo,
    Mtatsminda, Old Batumi -- are at 10.
    """

    geofabrik_name = 'georgia'
    country_name = 'საქართველო'
    country_slug = 'sakartvelo'
    iso2, iso3, un = 'GE', 'GEO', 268

    countrysubarea_levels = ('3', '4', '6')
    place_levels = ('8',)
    placesubarea_levels = ('10',)

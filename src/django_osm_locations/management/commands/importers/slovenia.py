from .nordic import AdminLevelImporter


class SloveniaImporter(AdminLevelImporter):
    """
    Slovenia.

    Read from the extract: 212 občine at level 8 and nothing above them that
    covers the country -- level 7 is eleven of the 58 upravne enote, drawn in
    places only, and a division that stops halfway would mislabel the rest.
    So a Slovenian address has a country and a municipality, no region.

    Below an občina: one krajevna skupnost at 9 and 96 naselja at 10; most
    settlements are place nodes.

    The extract reaches into Italy, Austria, Hungary and Croatia, which the
    country boundary filter excludes.
    """

    geofabrik_name = 'slovenia'
    country_name = 'Slovenija'
    country_slug = 'slovenija'
    iso2, iso3, un = 'SI', 'SVN', 705

    countrysubarea_levels = ()
    place_levels = ('8',)
    placesubarea_levels = ('9', '10')

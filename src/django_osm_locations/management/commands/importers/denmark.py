from .nordic import AdminLevelImporter


class DenmarkImporter(AdminLevelImporter):
    """
    Denmark.

    Read from the extract: five regions at level 4, carrying ISO3166-2 codes
    like DK-82, and 98 kommuner at level 7. There is no admin level for towns --
    Danish towns are place nodes -- so places come from level 7 plus the usual
    place tags.

    The extract also contains German Kreise and Gemeinden and Swedish kommuner
    along the borders; the country boundary filter in BaseImporter keeps them
    out.
    """

    geofabrik_name = 'denmark'
    country_name = 'Danmark'
    country_slug = 'danmark'
    iso2, iso3, un = 'DK', 'DNK', 208

    countrysubarea_levels = ('4',)
    place_levels = ('7',)
    placesubarea_levels = ('9', '10')

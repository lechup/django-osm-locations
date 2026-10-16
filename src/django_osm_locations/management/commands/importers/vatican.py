from .nordic import AdminLevelImporter


class VaticanImporter(AdminLevelImporter):
    """
    The Vatican.

    The Vatican has no Geofabrik extract of its own; it is read from Italy's,
    and its border keeps Rome out.

    The extract draws nothing inside it: no division, no place boundary. What
    it has are its streets and its few addresses, which take their place from
    the nearest place node.
    """

    geofabrik_name = 'italy'
    # its corner of Italy; reading the whole extract indexes every Italian node
    extract_bbox = (12.44, 41.898, 12.462, 41.909)
    name = 'vatican'
    country_name = 'Civitas Vaticana - Città del Vaticano'
    country_slug = 'civitas-vaticana-citta-del-vaticano'
    iso2, iso3, un = 'VA', 'VAT', 336

    countrysubarea_levels = ()
    place_levels = ()
    placesubarea_levels = ()

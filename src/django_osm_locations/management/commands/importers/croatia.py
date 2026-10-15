from .nordic import AdminLevelImporter


class CroatiaImporter(AdminLevelImporter):
    """
    Croatia.

    Read from the extract: 21 županije at level 4 and 555 gradovi and općine
    -- the municipalities -- at 7. The settlements, 6,754 naselja, are at 8,
    so that is where a place is. Zagreb's gradske četvrti and the mjesni
    odbori are at 9 and 10.

    The extract reaches into Slovenia, Hungary, Serbia, Bosnia and Herzegovina and Montenegro, which the country boundary filter
    excludes.
    """

    geofabrik_name = 'croatia'
    country_name = 'Hrvatska'
    country_slug = 'hrvatska'
    iso2, iso3, un = 'HR', 'HRV', 191

    countrysubarea_levels = ('4', '7')
    place_levels = ('8',)
    placesubarea_levels = ('9', '10')

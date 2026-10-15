from .nordic import AdminLevelImporter

#: A settlement, not a district: how Ukraine says which of its boundaries is a
#: place, because the level does not.
SETTLEMENT = {'city', 'town', 'village', 'hamlet'}
#: Tagged as an administrative boundary, but neither a settlement nor a part
#: of one.
NEITHER = {'allotments', 'isolated_dwelling', 'locality'}


class UkraineImporter(AdminLevelImporter):
    """
    Ukraine, within the border OSM draws for it, Crimea included.

    Two things set it apart.

    The occupied areas carry a second, Russian set of divisions over the
    Ukrainian one -- "Республика Крым" beside "Автономна Республіка Крим",
    and 246 сельские поселения at level 8. Those are tagged addr:country=RU or
    disputed_by=UA and left out; the Ukrainian ones say disputed_by=RU and
    are kept.

    And the level does not say what a boundary is. Kharkiv is a city at 8,
    Lviv at 9, Donetsk at 10, Sievierodonetsk at 11, each next to villages at
    the same level. So below the hromady the place tag decides: a city, town,
    village or hamlet is a place, and the rest at 8 to 10 -- city raiony,
    Kyiv's districts, quarters -- is part of one.

    Above that it is regular: 27 oblasti, Kyiv and Sevastopol at level 4,
    Sevastopol's raiony at 5, 136 raiony at 6 and 1,469 hromady at 7.

    The extract reaches into all seven neighbours, which the country boundary
    filter excludes.
    """

    geofabrik_name = 'ukraine'
    country_name = 'Україна'
    country_slug = 'ukraina'
    iso2, iso3, un = 'UA', 'UKR', 804

    #: The import keeps only the tags it needs, and these were not among
    #: them: without them the test below saw no Russian tag on anything, and
    #: the first run imported "Республика Крым" and 58 Russian okruga beside
    #: the Ukrainian divisions.
    whitelist_tags = AdminLevelImporter.whitelist_tags + ['addr:country', 'disputed_by']

    countrysubarea_levels = ('4', '5', '6', '7')
    place_levels = ('8', '9', '10', '11')
    placesubarea_levels = ('8', '9', '10')

    @staticmethod
    def _is_russian(tags):
        return tags.get('addr:country') == 'RU' or tags.get('disputed_by') == 'UA'

    @classmethod
    def _is_relation_countrysubarea(cls, osm_id, tags, members):
        return cls._is_admin(tags, cls.countrysubarea_levels) and not cls._is_russian(tags)

    @classmethod
    def _is_relation_place(cls, osm_id, tags, members):
        return (
            cls._is_admin(tags, cls.place_levels)
            and not cls._is_russian(tags)
            and tags.get('place') in SETTLEMENT
        )

    @classmethod
    def _is_relation_placesubarea(cls, osm_id, tags, members):
        return (
            cls._is_admin(tags, cls.placesubarea_levels)
            and not cls._is_russian(tags)
            and tags.get('place') not in SETTLEMENT | NEITHER
        )

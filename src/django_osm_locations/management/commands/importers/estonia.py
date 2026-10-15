from .nordic import AdminLevelImporter


class EstoniaImporter(AdminLevelImporter):
    """
    Estonia.

    Read from the extract: fifteen maakonnad at level 6 and 78 vallad and
    linnad at 7. Settlements are at 9 -- 4,715 külad, alevikud and towns --
    except Tallinn and Kohtla-Järve, drawn at 8; both are places. Tallinn's
    asumid are at 10.

    The extract reaches into Latvia and Russia, which the country boundary filter
    excludes.
    """

    geofabrik_name = 'estonia'
    country_name = 'Eesti'
    country_slug = 'eesti'
    iso2, iso3, un = 'EE', 'EST', 233

    countrysubarea_levels = ('6', '7')
    place_levels = ('8', '9')
    placesubarea_levels = ('10',)

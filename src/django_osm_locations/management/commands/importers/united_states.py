"""
The United States, a state at a time.

The country's extract is 12.2GB, two and a half times France's, which only
just went through on a 30GB machine. Geofabrik also cuts it into its 50
states, the District of Columbia, Puerto Rico and the US Virgin Islands, the
largest of them California at 1.3GB. So each of those is imported, packed and
loaded on its own, and together they are one country: every row's country is
the United States, and loading all 53 packs searches as one.

* A state's importer is named ``us-<extract>`` on the command line --
  ``us-california``, ``us-georgia``, which keeps the state apart from the
  country of Georgia -- and ``united-states`` names all of them.
* Its indexes and its pack carry ``us-<postal code>``: ``locations-streets-
  us-ca-...``, ``us-ca-2026-10-18``. A rebuild of one state replaces only that
  state under the alias.
* Its border filter is the state's own relation, found by its ISO 3166-2
  code: an extract reaches into its neighbours, and the country's border is
  not whole in any one of them.

Read from the extracts of Wyoming and Michigan: states at level 4 and
counties at 6 are divisions. Places are at 7 to 9 -- incorporated cities and
towns at 8, Michigan's townships at 8 with villages inside them at 9, Detroit
at 7 -- and an address takes the smallest one containing it. Most of the
country's unincorporated towns have no administrative boundary at all; the
Census Bureau's designated places are drawn as boundary=census and taken as
places too, so that Arlington, Virginia is somewhere. Neighbourhoods are at 10
and 11.
"""
from .nordic import AdminLevelImporter

#: Geofabrik's name for each part, and its ISO 3166-2 code.
STATES = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR',
    'california': 'CA', 'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE',
    'district-of-columbia': 'DC', 'florida': 'FL', 'georgia': 'GA', 'hawaii': 'HI',
    'idaho': 'ID', 'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
    'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
    'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS',
    'missouri': 'MO', 'montana': 'MT', 'nebraska': 'NE', 'nevada': 'NV',
    'new-hampshire': 'NH', 'new-jersey': 'NJ', 'new-mexico': 'NM', 'new-york': 'NY',
    'north-carolina': 'NC', 'north-dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK',
    'oregon': 'OR', 'pennsylvania': 'PA', 'puerto-rico': 'PR', 'rhode-island': 'RI',
    'south-carolina': 'SC', 'south-dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX',
    'us-virgin-islands': 'VI', 'utah': 'UT', 'vermont': 'VT', 'virginia': 'VA',
    'washington': 'WA', 'west-virginia': 'WV', 'wisconsin': 'WI', 'wyoming': 'WY',
}

GROUP = 'united-states'

#: Census-designated places: a town with no government of its own, so no
#: administrative boundary, but a name and an outline everyone uses.
CENSUS = 'census'


class UnitedStatesImporter(AdminLevelImporter):
    """One state's part of the United States; see the module for the whole."""

    geofabrik_path = 'north-america/us/'
    group = GROUP
    country_name = 'United States'
    country_slug = 'united-states'
    iso2, iso3, un = 'US', 'USA', 840
    #: set per state
    state_code = None

    countrysubarea_levels = ('4', '6')
    place_levels = ('7', '8', '9')
    placesubarea_levels = ('10', '11')

    #: the state relation is found by its ISO 3166-2 code, which the parse
    #: would otherwise drop
    whitelist_tags = AdminLevelImporter.whitelist_tags + ['ISO3166-2', 'border_type']

    #: The extract the state borders are taken from, whole: a state's own
    #: extract lacks the ways on its state line (Wyoming: 230 of 299), and
    #: the counties along it share them.
    borders_from = 'north-america/us'
    #: The country's extract stops 150 m short of Alaska's border on the
    #: Arctic coast at the Yukon line: one way, and the mainland's ring open
    #: without it.
    borders_fill_from = ('north-america/canada/yukon',)

    @staticmethod
    def is_border_relation(tags):
        """
        What borders_from is read for: the states, and the counties, since a
        county on the state line can lack a way of its own the state's
        relation does not have -- Goshen and Niobrara in Wyoming.
        """
        if tags.get('boundary') != 'administrative':
            return False
        if tags.get('admin_level') == '4':
            return (tags.get('ISO3166-2') or '').startswith('US-')
        return tags.get('admin_level') == '6'

    @classmethod
    def index_token_for(cls):
        return 'us-{}'.format(cls.state_code.lower())

    def _is_country_boundary(self, tags):
        """The state's own relation, not the country's: no state holds that whole."""
        return (
            tags.get('admin_level') == '4'
            and (tags.get('ISO3166-2') or '').upper() == 'US-{}'.format(self.state_code)
        )

    @classmethod
    def _is_relation_place(cls, osm_id, tags, members):
        if tags.get('boundary') == CENSUS:
            # a designated place, place=locality; not a census county
            # division, which is a share of a county and as big as one
            return (
                'name' in tags and 'place' in tags
                and tags.get('border_type') != 'census_county_division'
            )
        return super()._is_relation_place(osm_id, tags, members)


def _state(extract, code):
    return type(
        'UnitedStates{}Importer'.format(code),
        (UnitedStatesImporter,),
        {
            '__module__': __name__,
            '__doc__': 'The United States: {} ({}).'.format(extract.replace('-', ' ').title(), code),
            'geofabrik_name': extract,
            'name': 'us-{}'.format(extract),
            'state_code': code,
        },
    )


for _extract, _code in STATES.items():
    _cls = _state(_extract, _code)
    globals()[_cls.__name__] = _cls
del _extract, _code, _cls

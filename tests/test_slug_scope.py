"""
Slugs are unique within a country, not across the world.

That is what lets each country's index be built, published and merged with
another's without either one's public identifiers moving: adding Denmark
cannot rename a Polish street. Measured on the three imported countries, only
60 base slugs would collide across countries out of ~422,000 unqualified rows
-- and they are mostly junk names, cycle trails called "jurassic-park" and
short Danish-English village names like "dale".

The price is that a slug no longer identifies a row globally. The detail
endpoint has to account for that once several countries are loaded together.
"""
import pytest
from django.contrib.gis.geos import LineString, MultiLineString, MultiPolygon
from django.core.management import call_command

from django_osm_locations.management.commands.locations_recalculate_slugs import (
    Command as Slugs,
)
from django_osm_locations.models import (
    Country, CountrySubarea, Place, PlaceSubarea, Street,
)

from .conftest import square

pytestmark = pytest.mark.django_db


def street(name, country_slug, offset=0.0):
    return Street.objects.create(
        name=name,
        lines=MultiLineString(LineString((offset, offset), (offset + 0.1, offset + 0.1))),
        country=[[country_slug.title(), country_slug]],
    )


class TestTheScopeExpression:
    def test_it_reads_the_country_after_the_placeholder_is_stripped(self):
        street('Łąkowa', 'polska')
        assert dict(Slugs.scopes_for(Street)) == {'lakowa': 'polska'}

    def test_it_reads_the_country_while_the_placeholder_is_still_there(self):
        """
        Mid-walk the arrays carry ['',''] at position 1 and the real ancestor
        at 2; after the strip it is at 1. The expression has to cover both,
        because the slug pass runs before the strip.
        """
        s = street('Łąkowa', 'polska')
        Street.objects.filter(pk=s.pk).update(
            country=[['', ''], ['Polska', 'polska']])
        assert dict(Slugs.scopes_for(Street)) == {'lakowa': 'polska'}

    def test_a_row_belonging_to_no_country_gets_its_own_scope(self):
        """5,983 streets fall outside every border; they are not Poland."""
        s = street('Łąkowa', 'polska')
        Street.objects.filter(pk=s.pk).update(country=[])
        scopes = dict(Slugs.scopes_for(Street))
        assert scopes['lakowa'] != 'polska'

    def test_countries_themselves_stay_globally_scoped(self):
        """
        Country slugs are the one thing that must stay unique worldwide --
        they are what every other scope is keyed by. Countries are seeded by
        migration, so there are rows here without creating any.
        """
        assert Country.objects.exists()
        assert {scope for _, scope in Slugs.scopes_for(Country)} == {''}


class TestDuplicatesAreFoundPerCountry:
    def test_two_countries_are_not_duplicates_of_each_other(self):
        street('Dale', 'polska', offset=0.0)
        street('Dale', 'danmark', offset=5.0)
        call_command('locations_recalculate_slugs', 'street')
        assert sorted(Street.objects.values_list('slug', flat=True)) == ['dale', 'dale']

    def test_two_in_one_country_are(self):
        street('Dale', 'polska', offset=0.0)
        street('Dale', 'polska', offset=5.0)
        call_command('locations_recalculate_slugs', 'street')
        found = sorted(Street.objects.values_list('slug', flat=True))
        assert found[0] == 'dale'
        assert found[1] != 'dale', found

    def test_the_grouping_is_keyed_by_country_and_slug(self):
        street('Dale', 'polska', offset=0.0)
        street('Dale', 'danmark', offset=5.0)
        street('Dale', 'polska', offset=1.0)
        found = {(scope, slug): sorted(ids)
                 for scope, slug, ids in Slugs.duplicates_for(Street)}
        # only Poland has a duplicate; Denmark's lone Dale is not one
        assert list(found) == [('polska', 'dale')]
        assert len(found[('polska', 'dale')]) == 2


class TestAddingACountryLeavesTheOthersAlone:
    def test_a_polish_slug_survives_denmark_arriving(self):
        """
        The whole point of the scope. Under global uniqueness, importing
        Denmark could rename a Polish street and break a published URL.
        """
        street('Dale', 'polska', offset=0.0)
        call_command('locations_recalculate_slugs', 'street')
        before = Street.objects.get(country__contains=[['Polska', 'polska']]).slug

        street('Dale', 'danmark', offset=5.0)
        call_command('locations_recalculate_slugs', 'street')
        after = Street.objects.filter(
            country__contains=[['Polska', 'polska']]).values_list('slug', flat=True)

        assert before == 'dale'
        assert list(after) == ['dale']


class TestItIsOnePassNotOnePerCountry:
    def test_the_scope_comes_from_one_query(self):
        """
        The denormalised arrays carry no GIN index and `country` is a
        two-dimensional array, so a filter per country would be a sequential
        scan per country -- ten of them over 2.8 million streets.
        """
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        for i in range(3):
            street('Łąkowa {}'.format(i), 'polska', offset=i)

        with CaptureQueriesContext(connection) as captured:
            list(Slugs.scopes_for(Street))

        assert len(captured) == 1, [q['sql'][:80] for q in captured]


class TestEveryModelCanBeAsked:
    """
    Country has no country to be scoped by, so its scope is a constant -- and a
    bare constant cannot be named in GROUP BY, which PostgreSQL rejects as
    "non-integer constant in GROUP BY". Nothing covered that: the tests asked
    scopes_for(Country), which parameterises the constant and is fine, and never
    duplicates_for(Country). locations_recalculate_all runs the Country pass
    first, so the whole recalculation died four hours in.
    """

    @pytest.mark.parametrize('model', [
        Country, CountrySubarea, Place, PlaceSubarea, Street,
    ])
    def test_duplicates_for_runs(self, model):
        assert isinstance(Slugs.duplicates_for(model), list)

    @pytest.mark.parametrize('model', [
        Country, CountrySubarea, Place, PlaceSubarea, Street,
    ])
    def test_scopes_for_runs(self, model):
        assert isinstance(list(Slugs.scopes_for(model)), list)

    def test_countries_sharing_a_slug_are_still_duplicates(self):
        """
        Being globally scoped does not mean unscoped: two countries with the
        same base slug must still be separated.
        """
        for iso2, iso3, un in (('T1', 'TS1', 901), ('T2', 'TS2', 902)):
            Country.objects.create(name='Testland', slug='testland',
                                   iso2=iso2, iso3=iso3, un=un)
        found = {slug for _scope, slug, _ids in Slugs.duplicates_for(Country)}
        assert 'testland' in found

    def test_the_whole_recalculation_gets_through_the_country_pass(self):
        """The command that died: recalculate_all's first slug pass."""
        call_command('locations_recalculate_slugs', 'Country')
        assert Country.objects.exclude(slug='').count() == Country.objects.count()

"""
One index per country, one alias over all of them.

An alias in OpenSearch can point at several indexes, and that is the whole
mechanism by which countries published separately are searched as one. What it
costs is care in promote(): rebuilding Poland must leave Denmark under the
alias, or each import would silently evict the last.
"""
import pytest

from django_osm_locations.models import Country
from django_osm_locations.search.indexer import Reindexer
from django_osm_locations.search.indexes import INDEXES

pytestmark = pytest.mark.django_db


class FakeClient:
    """Enough of the client to exercise naming and alias arithmetic."""

    def __init__(self):
        self.actions = []

    def indices(self):  # pragma: no cover - placeholder
        raise NotImplementedError


def reindexer(country=None, index='streets', suffix='20260924', prefix='locations'):
    return Reindexer(INDEXES[index], client=FakeClient(), prefix=prefix,
                     suffix=suffix, country=country, show_progress=False)


@pytest.fixture
def poland():
    return Country.objects.get(slug='polska')


@pytest.fixture
def denmark():
    return Country.objects.get(slug='danmark')


class TestNaming:
    def test_a_country_build_carries_its_iso_code(self, poland):
        assert reindexer(poland).index_name == 'locations-streets-pl-20260924'

    def test_a_full_build_is_named_as_before(self):
        assert reindexer().index_name == 'locations-streets-20260924'

    def test_two_countries_get_distinct_indexes(self, poland, denmark):
        assert reindexer(poland).index_name != reindexer(denmark).index_name

    def test_they_share_one_alias(self, poland, denmark):
        assert reindexer(poland).alias == reindexer(denmark).alias == 'locations-streets'


class TestWhichIndexesAreReplaced:
    """
    The part that makes merging possible. A country rebuild supersedes only
    that country; a full rebuild supersedes everything.
    """

    def test_a_country_replaces_its_own_earlier_index(self, poland):
        assert reindexer(poland).replaces('locations-streets-pl-20260101') is True

    def test_a_country_leaves_another_country_alone(self, poland, denmark):
        assert reindexer(poland).replaces('locations-streets-dk-20260101') is False

    def test_a_country_leaves_a_full_index_alone(self, poland):
        """
        It covers rows this build does not, so evicting it would lose them.
        """
        assert reindexer(poland).replaces('locations-streets-20260101') is False

    def test_it_never_replaces_itself(self, poland):
        r = reindexer(poland)
        assert r.replaces(r.index_name) is False

    def test_another_type_is_never_touched(self, poland):
        assert reindexer(poland).replaces('locations-places-pl-20260101') is False

    def test_a_full_rebuild_replaces_everything(self):
        r = reindexer()
        assert r.replaces('locations-streets-20260101') is True
        assert r.replaces('locations-streets-pl-20260101') is True
        assert r.replaces('locations-streets-dk-20260101') is True

    def test_a_prefix_that_merely_starts_the_same_is_not_a_match(self, poland):
        """locations-streets-plus-... is not Poland."""
        assert reindexer(poland).replaces('locations-streets-plus-20260101') is False


class TestWhichRowsAreIndexed:
    def test_a_country_build_filters_by_the_denormalised_country(self, poland):
        sql = str(reindexer(poland).queryset().query)
        assert 'country' in sql.lower()

    def test_a_full_build_does_not_filter(self):
        from django_osm_locations.search.indexes import INDEXES as I
        assert str(reindexer().queryset().query) == str(I['streets'].queryset().query)

    def test_the_countries_index_matches_on_its_own_row(self, poland):
        """
        Country carries no denormalised country array -- it is what the arrays
        point at -- so it is matched on the primary key instead.
        """
        qs = reindexer(poland, index='countries').queryset()
        assert list(qs.values_list('pk', flat=True)) == [poland.pk]

    def test_the_count_follows_the_same_filter(self, poland):
        r = reindexer(poland, index='countries')
        assert r.database_count() == 1

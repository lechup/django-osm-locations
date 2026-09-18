"""
Resolving duplicate slugs.

Slugs are public -- places are served as subdomains -- so a duplicate used to
become "station-road-25986049", unique but unusable. It is now qualified by
where the thing is.
"""
import pytest
from django.contrib.gis.geos import LineString, MultiLineString, MultiPolygon
from django.core.management import call_command

from django_osm_locations.models import CountrySubarea, Place, Street

from .conftest import square

pytestmark = pytest.mark.django_db


def street(name, place=None, offset=0.0, merged=False):
    obj = Street.objects.create(
        name=name,
        lines=MultiLineString(LineString((offset, offset), (offset + 0.1, offset + 0.1))),
        place=[[place.title(), place]] if place else [],
    )
    if merged:
        obj.mark_as_merged()
    return obj


def place(name, x, y, population=0, size=1.0):
    return Place.objects.create(
        name=name, border=MultiPolygon(square(x, y, size)), population=population,
    )


def slugs(model=Street):
    return dict(model.objects.values_list('name', 'slug'))


class TestStreets:
    def test_unique_names_keep_their_bare_slug(self):
        street('Abbey Road', 'douglas')
        call_command('locations_recalculate_slugs', 'street')
        assert Street.objects.get().slug == 'abbey-road'

    def test_duplicates_are_qualified_by_their_place(self):
        street('Station Road', 'ramsey', offset=0.0)
        street('Station Road', 'peel', offset=1.0)
        call_command('locations_recalculate_slugs', 'street')
        assert set(Street.objects.values_list('slug', flat=True)) == {
            'station-road', 'station-road-peel',
        } or set(Street.objects.values_list('slug', flat=True)) == {
            'station-road', 'station-road-ramsey',
        }

    def test_only_the_largest_keeps_the_bare_slug(self):
        """The longest street wins; the rest are qualified."""
        long_one = street('Station Road', 'ramsey', offset=0.0)
        street('Station Road', 'peel', offset=5.0)
        call_command('locations_recalculate_slugs', 'street')
        bare = Street.objects.filter(slug='station-road')
        assert bare.count() == 1

    def test_no_opaque_id_when_a_place_can_separate_them(self):
        for i, town in enumerate(['ramsey', 'peel', 'douglas', 'castletown']):
            street('Station Road', town, offset=i)
        call_command('locations_recalculate_slugs', 'street')
        for slug in Street.objects.values_list('slug', flat=True):
            assert not slug.split('-')[-1].isdigit(), slug

    def test_second_street_in_the_same_town_still_gets_a_readable_slug(self):
        street('Cronk Y Berry', 'douglas', offset=0.0)
        street('Cronk Y Berry', 'douglas', offset=1.0)
        call_command('locations_recalculate_slugs', 'street')
        assert sorted(Street.objects.values_list('slug', flat=True)) == [
            'cronk-y-berry', 'cronk-y-berry-douglas',
        ]

    def test_falls_back_to_the_osm_id_when_geography_is_exhausted(self):
        """
        Three of the same name in the same town: the bare slug and the
        town-qualified one are taken, and nothing else distinguishes them.
        """
        for offset in (0.0, 1.0, 2.0):
            street('Cronk Y Berry', 'douglas', offset=offset)
        call_command('locations_recalculate_slugs', 'street')
        found = set(Street.objects.values_list('slug', flat=True))
        assert {'cronk-y-berry', 'cronk-y-berry-douglas'} <= found
        leftover = found - {'cronk-y-berry', 'cronk-y-berry-douglas'}
        assert len(leftover) == 1
        assert leftover.pop().rsplit('-', 1)[1].isdigit()

    def test_slugs_stay_unique(self):
        for i, town in enumerate(['ramsey', 'peel', 'ramsey', 'peel']):
            street('Shore Road', town, offset=i)
        call_command('locations_recalculate_slugs', 'street')
        values = list(Street.objects.values_list('slug', flat=True))
        assert len(values) == len(set(values))


class TestMergedRows:
    def test_merged_rows_never_take_the_readable_slug(self):
        """
        Regression: a merged row -- a duplicate the import already resolved, and
        absent from the search index -- was outranking the live street and
        taking "coast-road-german", pushing the real one onto an OSM id.
        """
        live = street('Coast Road', 'german', offset=0.0)
        street('Coast Road', 'german', offset=5.0, merged=True)
        street('Coast Road', 'peel', offset=9.0)

        call_command('locations_recalculate_slugs', 'street')

        live.refresh_from_db()
        assert not live.slug.rsplit('-', 1)[-1].isdigit(), \
            'a live street should not be pushed onto an id by a merged one'

    def test_live_rows_are_ranked_before_merged_ones(self):
        merged = street('Mill Road', 'peel', offset=0.0, merged=True)
        live = street('Mill Road', 'ramsey', offset=5.0)
        call_command('locations_recalculate_slugs', 'street')
        live.refresh_from_db(); merged.refresh_from_db()
        assert live.slug == 'mill-road'
        assert merged.slug != 'mill-road'


class TestPlaces:
    def test_duplicate_places_are_qualified_by_the_nearest_larger_place(self):
        # keep each village inside the search radius of its own city, and the
        # two pairs far enough apart that neither city is the other's nearest
        place('Krakow', 0, 0, population=800_000, size=0.5)
        small_near_krakow = place('Nowa Wies', 0.6, 0.6, population=500, size=0.1)
        place('Gdansk', 20, 20, population=470_000, size=0.5)
        small_near_gdansk = place('Nowa Wies', 20.6, 20.6, population=400, size=0.1)

        call_command('locations_recalculate_slugs', 'place')

        small_near_krakow.refresh_from_db()
        small_near_gdansk.refresh_from_db()
        qualified = {small_near_krakow.slug, small_near_gdansk.slug}
        assert 'nowa-wies' in qualified, 'the larger of the two keeps the bare slug'
        other = (qualified - {'nowa-wies'}).pop()
        assert other in ('nowa-wies-krakow', 'nowa-wies-gdansk'), other
        assert not other.rsplit('-', 1)[-1].isdigit()

    def test_the_more_populous_duplicate_keeps_the_bare_slug(self):
        big = place('Springfield', 0, 0, population=100_000, size=1.0)
        place('Springfield', 10, 10, population=900, size=1.0)
        call_command('locations_recalculate_slugs', 'place')
        big.refresh_from_db()
        assert big.slug == 'springfield'


class TestOtherLevels:
    def test_country_subareas_qualify_by_country(self):
        CountrySubarea.objects.create(
            name='Middle', border=MultiPolygon(square(0, 0)),
            country=[['Isle of Man', 'isle-of-man']],
        )
        CountrySubarea.objects.create(
            name='Middle', border=MultiPolygon(square(9, 9)),
            country=[['Polska', 'polska']],
        )
        call_command('locations_recalculate_slugs', 'countrysubarea')
        found = sorted(CountrySubarea.objects.values_list('slug', flat=True))
        assert found[0] == 'middle'
        assert found[1] in ('middle-polska', 'middle-isle-of-man'), found[1]

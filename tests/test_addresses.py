"""
Addresses, and what they let us stop guessing.

An address states which street and settlement it belongs to. 64% of Poland's
addressed features and 80% of Britain's name both, against 0.6% of roads
carrying addr:city themselves -- so for most streets the association is
written down rather than inferred from proximity.
"""
import pytest
from django.contrib.gis.geos import Point

from django_osm_locations.api.queries import DEFAULT_TYPE_BOOSTS
from django_osm_locations.management.commands.importers.base import BaseImporter
from django_osm_locations.management.commands.locations_recalculate_denormalized_fields import (
    Command as Denorm,
)
from django_osm_locations.models import Address
from django_osm_locations.search.indexes import INDEXES


class TestWhatCountsAsAnAddress:
    def test_a_house_number_on_a_street(self):
        assert BaseImporter._is_address(
            {'addr:housenumber': '5', 'addr:street': 'Łąkowa'}) is True

    def test_a_house_number_in_a_village_with_no_streets(self):
        """35% of Poland's addresses and 0.3% of Britain's are addressed this way."""
        assert BaseImporter._is_address(
            {'addr:housenumber': '12', 'addr:place': 'Kowale'}) is True

    def test_a_number_with_nothing_to_hang_it_on(self):
        assert BaseImporter._is_address({'addr:housenumber': '5'}) is False

    def test_a_street_without_a_number_is_not_an_address(self):
        assert BaseImporter._is_address({'addr:street': 'Łąkowa'}) is False


class TestWhatIsStored:
    def test_the_name_is_what_somebody_would_type(self):
        d = BaseImporter._address_defaults(
            {'addr:housenumber': '5', 'addr:street': 'Łąkowa', 'addr:city': 'Gdańsk'})
        assert d['name'] == 'Łąkowa 5'
        assert d['street_name'] == 'Łąkowa'
        assert d['settlement_name'] == 'Gdańsk'

    def test_a_village_address_is_named_by_its_settlement(self):
        d = BaseImporter._address_defaults(
            {'addr:housenumber': '12', 'addr:place': 'Kowale'})
        assert d['name'] == 'Kowale 12'
        assert d['settlement_name'] == 'Kowale'

    def test_addr_place_is_a_settlement_too(self):
        d = BaseImporter._address_defaults(
            {'addr:housenumber': '1', 'addr:street': 'X', 'addr:place': 'Kowale'})
        assert d['settlement_name'] == 'Kowale'

    def test_the_tags_survive_the_parse_filter(self):
        for key in ('addr:housenumber', 'addr:street', 'addr:city', 'addr:place'):
            assert BaseImporter._wanted_tag(key) is True, key


class TestSlugsAreDerivedNotDisputed:
    """
    8.5 million addresses for Poland alone. A street slug is already unique,
    so a house number on it is unique too -- there is nothing to disambiguate.
    """

    def test_the_slug_is_the_street_plus_the_number(self):
        a = Address(name='Łąkowa 5', house_number='5',
                    street=[['Łąkowa', 'lakowa-gdansk']])
        assert a.slugify == 'lakowa-gdansk-5'

    def test_a_village_address_hangs_off_its_place(self):
        a = Address(name='Kowale 12', house_number='12',
                    place=[['Kowale', 'kowale']])
        assert a.slugify == 'kowale-12'

    def test_with_no_parent_it_falls_back_to_the_name(self):
        assert Address(name='Kowale 12', house_number='12').slugify == 'kowale-12'

    def test_addresses_have_no_qualifier_cascade(self):
        """
        The slug command disambiguates only what it has qualifiers for, and an
        address needs none -- its slug is unique the moment it is derived.
        """
        from django_osm_locations.management.commands.locations_recalculate_slugs import (
            Command as Slugs,
        )

        assert Address not in Slugs.QUALIFIERS


class TestIndexing:
    def test_addresses_have_an_index(self):
        assert 'addresses' in INDEXES

    def test_an_address_carries_no_geometry(self):
        """A point has no extent, which is what makes 8.5M of them affordable."""
        assert INDEXES['addresses'].geometry_field is None

    def test_the_document_is_built_without_one(self):
        a = Address(id=1, name='Łąkowa 5', slug='lakowa-gdansk-5', house_number='5',
                    street_name='Łąkowa', settlement_name='Gdańsk',
                    center=Point(18.6, 54.35), street=[['Łąkowa', 'lakowa-gdansk']],
                    place=[['Gdańsk', 'gdansk']], bbox=[])
        doc = INDEXES['addresses'].document(a)
        assert doc['geometry'] is None
        assert doc['house_number'] == '5'
        assert doc['center'] == {'lon': 18.6, 'lat': 54.35}

    def test_the_parent_path_can_be_printed(self):
        """Polska > pomorskie > Gdańsk > Łąkowa > Łąkowa 5."""
        a = Address(id=1, name='Łąkowa 5', slug='x', house_number='5',
                    center=Point(18.6, 54.35), bbox=[],
                    country=[['Polska', 'polska']],
                    place=[['Gdańsk', 'gdansk']],
                    street=[['Łąkowa', 'lakowa-gdansk']])
        doc = INDEXES['addresses'].document(a)
        assert doc['ancestry_names'] == ['Polska', 'Gdańsk', 'Łąkowa']

    def test_street_is_a_denormalised_level(self):
        assert 'street' in Address.denorm_fields


class TestRanking:
    def test_addresses_rank_below_streets(self):
        """Searching a street name should not bury it under its own houses."""
        assert DEFAULT_TYPE_BOOSTS['addresses'] < DEFAULT_TYPE_BOOSTS['streets']


class TestSettlementFromAddresses:
    def test_the_modal_settlement_wins(self):
        """A long road can carry addresses from more than one village."""
        sql = Denorm.get_sql_for_street_settlement_from_addresses()
        assert 'count(*) AS votes' in sql
        assert 'ORDER BY v.street_id, v.votes DESC' in sql

    def test_it_matches_on_the_street_name(self):
        sql = Denorm.get_sql_for_street_settlement_from_addresses()
        assert 'a.street_name = st.name' in sql

    def test_only_addresses_near_the_street_vote(self):
        sql = Denorm.get_sql_for_street_settlement_from_addresses()
        assert 'ST_DWithin' in sql

    def test_the_radius_is_a_building_setback_not_a_neighbourhood(self):
        assert Denorm.ADDRESS_MATCH_METRES <= 100

    def test_addresses_are_consulted_before_proximity(self):
        """
        An address states the settlement; nearest-place guesses it. Reading
        beats guessing, so the guess only fills what is left.
        """
        import inspect

        source = inspect.getsource(Denorm.update_street_place_field)
        assert source.index('from its own addresses') < source.index('nearest place')


class TestAnAddressHasABbox:
    """
    LocationBase carries no bbox hook -- the bordered and multiline bases each
    supply their own -- and Address inherits neither. Every address document
    shipped an empty bbox until it grew one of its own.
    """

    def test_the_hook_exists(self):
        assert '_presave_bbox' in Address._save_hooks()

    @pytest.mark.django_db
    def test_the_bbox_is_around_the_point(self):
        a = Address(name='Łąkowa 5', house_number='5', center=Point(18.6, 54.35))
        a.save()
        assert a.bbox == a.center_bbox
        assert a.bbox != []


@pytest.mark.django_db
class TestSlugIsDerivedAfterTheStreetIsKnown:
    """
    _presave_slug writes a slug only when there is none -- at import, before
    the street array exists. Every address came out as "lakowa-5", and nothing
    later touched it, because Address is kept out of the disambiguation pass
    on purpose. Derived still has to be derived after the link.
    """

    def test_the_import_path_alone_gives_an_unqualified_slug(self):
        """This is the bug: reproduce it before showing it fixed."""
        a = Address(name='Łąkowa 5', house_number='5', center=Point(18.6, 54.35))
        a.save()
        assert a.slug == 'lakowa-5'

    def test_the_pass_qualifies_it_by_the_linked_street(self):
        from django.db import connection

        a = Address(name='Łąkowa 5', house_number='5', center=Point(18.6, 54.35))
        a.save()
        Address.objects.filter(pk=a.pk).update(street=[['Łąkowa', 'lakowa-gdansk']])

        Denorm.update_address_slugs()

        a.refresh_from_db()
        assert a.slug == 'lakowa-gdansk-5'

    def test_a_village_address_falls_back_to_its_place(self):
        a = Address(name='Kowale 12', house_number='12', center=Point(18.6, 54.35))
        a.save()
        Address.objects.filter(pk=a.pk).update(place=[['Kowale', 'kowale']])

        Denorm.update_address_slugs()

        a.refresh_from_db()
        assert a.slug == 'kowale-12'

    def test_with_no_parent_it_keeps_the_name_slug(self):
        a = Address(name='Kowale 12', house_number='12', center=Point(18.6, 54.35))
        a.save()
        Denorm.update_address_slugs()
        a.refresh_from_db()
        assert a.slug == 'kowale-12'

    @pytest.mark.parametrize('number', ['5', '12a', '5/7', '3 B'])
    def test_sql_and_python_agree(self, number):
        """
        Two spellings of the same rule, and a mismatch would fail silently:
        the row would carry one slug and the model would compute another.
        """
        a = Address(name='Łąkowa {}'.format(number), house_number=number,
                    center=Point(18.6, 54.35))
        a.save()
        Address.objects.filter(pk=a.pk).update(street=[['Łąkowa', 'lakowa-gdansk']])
        Denorm.update_address_slugs()
        a.refresh_from_db()
        assert a.slug == a.slugify


class TestTheSettlementJoinIsDeterministic:
    """
    A name alone does not pick a place: 44% of Polish places share theirs
    with another, and a plain join on the name matched every Kowale there is
    -- an arbitrary one won. The nearest namesake wins now.
    """

    def test_it_picks_the_nearest_place_of_that_name(self):
        sql = Denorm.get_sql_for_street_settlement_from_addresses()
        assert 'p.name = ranked.settlement_name' in sql
        assert 'ORDER BY ST_Distance(p.center, st2.center)' in sql
        assert 'LIMIT 1' in sql

    def test_it_is_a_lateral_not_a_join(self):
        """A join fans out; a lateral with LIMIT 1 yields exactly one row."""
        sql = Denorm.get_sql_for_street_settlement_from_addresses()
        assert 'CROSS JOIN LATERAL' in sql
        assert 'JOIN "locations_place" p\n' not in sql

    @pytest.mark.django_db
    def test_two_places_with_one_name_resolve_to_the_near_one(self):
        from django.db import connection

        from django_osm_locations.models import Place, Street
        from django.contrib.gis.geos import LineString, MultiLineString

        near = Place.objects.create(name='Kowale', slug='kowale', center=Point(18.60, 54.35))
        far = Place.objects.create(name='Kowale', slug='kowale-2', center=Point(19.60, 55.35))
        street = Street.objects.create(
            name='Łąkowa', slug='lakowa',
            lines=MultiLineString(LineString((18.600, 54.350), (18.601, 54.351))),
            center=Point(18.6005, 54.3505),
        )
        Address.objects.create(name='Łąkowa 1', house_number='1', street_name='Łąkowa',
                               settlement_name='Kowale', center=Point(18.6006, 54.3506))

        with connection.cursor() as cursor:
            cursor.execute(Denorm.get_sql_for_street_settlement_from_addresses(),
                           [Denorm.ADDRESS_MATCH_METRES])

        street.refresh_from_db()
        assert street.place == [['Kowale', 'kowale']]
        assert street.place != [['Kowale', 'kowale-2']]


class TestSlugsRunAfterTheStrip:
    def test_the_walk_derives_address_slugs_after_cleaning(self):
        """
        Until the placeholder is stripped, element [1] is ['',''] and the real
        parent sits at [2]; deriving before that would read an empty slug.
        """
        from unittest import mock

        from django_osm_locations.management.commands.locations_recalculate_all import (
            Command as RecalculateAll,
        )

        order = []
        with mock.patch(
            'django_osm_locations.management.commands.locations_recalculate_all.call_command'
        ), mock.patch.object(Denorm, 'clean_denorm_fields_before_update'), \
                mock.patch.object(Denorm, 'inherit_address_ancestry_from_place'), \
                mock.patch.object(Denorm, 'inherit_street_ancestry_from_place'), \
                mock.patch.object(Denorm, 'update_street_place_field'), \
                mock.patch.object(Denorm, 'update_address_street_field',
                                  side_effect=lambda: order.append('link')), \
                mock.patch.object(Denorm, 'clean_denorm_fields_after_update',
                                  side_effect=lambda: order.append('clean')), \
                mock.patch.object(Denorm, 'update_address_slugs',
                                  side_effect=lambda: order.append('slugs')):
            RecalculateAll().handle()

        assert order == ['link', 'clean', 'slugs']


class TestParameterisedSqlIsSafeToExecute:
    """
    psycopg2 scans the whole SQL string for placeholders, comments included.
    A stray percent character shifts the parameter count and the statement
    fails at execute time -- which is how a comment reading "44% of places"
    took down this pass, and a follow-up comment containing a literal "%s"
    took it down again. Pin the count, so the next comment cannot.
    """
    import re as _re
    STRAY = _re.compile(r'%[^s%(]')

    @pytest.mark.parametrize('sql,params', [
        (Denorm.get_sql_for_street_settlement_from_addresses(), 1),
        (Denorm.get_sql_for_address_street_link(), 1),
        (Denorm.get_sql_for_street_place_field(), 1),
        (Denorm.get_sql_for_address_slugs(), 0),
    ])
    def test_placeholder_count_matches_the_parameters_passed(self, sql, params):
        assert sql.count('%s') == params

    @pytest.mark.parametrize('sql', [
        Denorm.get_sql_for_street_settlement_from_addresses(),
        Denorm.get_sql_for_address_street_link(),
        Denorm.get_sql_for_street_place_field(),
        Denorm.get_sql_for_address_slugs(),
    ])
    def test_no_stray_percent_character(self, sql):
        assert self.STRAY.search(sql) is None, self.STRAY.search(sql)

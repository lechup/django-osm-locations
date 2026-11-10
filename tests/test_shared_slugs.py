"""
Houses sharing a slug, each given one of its own.

The cases are the packs': kearns-5935 in Utah, seven houses on three streets
none was linked to; Østergade 12 in two villages of Langeland; Furth an der
Triesting 46, twice, seven kilometres apart and on no street.
"""
from django_osm_locations.utils.shared_slugs import SLUG_LENGTH, qualified, qualify


def house(osm_id, **fields):
    return dict({'linked': False, 'street_name': '', 'settlement': '', 'district': '',
                 'nearest': None, 'osm_id': osm_id}, **fields)


class TestWhatTellsThemApart:
    def test_the_street_each_names(self):
        houses = [house(1, street_name='South Dewdrops Drive'),
                  house(2, street_name='South Salem Avenue')]
        assert qualify('kearns-5935', houses, set()) == [
            'kearns-5935-south-dewdrops-drive', 'kearns-5935-south-salem-avenue']

    def test_not_the_street_they_are_all_on(self):
        houses = [house(1, linked=True, street_name='Østergade', settlement='Rudkøbing'),
                  house(2, linked=True, street_name='Østergade', settlement='Bagenkop')]
        assert qualify('ostergade-12', houses, set()) == [
            'ostergade-12-rudkobing', 'ostergade-12-bagenkop']

    def test_the_district_where_the_settlement_is_one(self):
        houses = [house(1, linked=True, settlement='Lisboa', district='Alfama'),
                  house(2, linked=True, settlement='Lisboa', district='Belém')]
        assert qualify('rua-direita-3', houses, set()) == [
            'rua-direita-3-lisboa', 'rua-direita-3-belem']

    def test_what_the_slug_has_already_adds_nothing(self):
        """Furth an der Triesting 46 in Furth an der Triesting."""
        houses = [house(1, settlement='Furth an der Triesting', nearest='Aigen'),
                  house(2, settlement='Furth an der Triesting', nearest='Steinhof')]
        assert qualify('furth-an-der-triesting-46', houses, set()) == [
            'furth-an-der-triesting-46-aigen', 'furth-an-der-triesting-46-steinhof']

    def test_the_osm_id_when_nothing_else_does(self):
        houses = [house(11, settlement='Gaal'), house(12, settlement='Gaal')]
        assert qualify('gaal-20', houses, set()) == ['gaal-20-11', 'gaal-20-12']

    def test_never_a_slug_another_house_has(self):
        houses = [house(1, street_name='A Street'), house(2, street_name='B Street')]
        assert qualify('town-5', houses, {'town-5-a-street'}) == ['town-5-1', 'town-5-b-street']

    def test_never_the_same_twice(self):
        houses = [house(1, settlement='Gaal'), house(1, settlement='Gaal')]
        slugs = qualify('gaal-20', houses, set())
        assert len(set(slugs)) == 2


def test_the_base_gives_way_to_the_qualifier():
    slug = qualified('a' * SLUG_LENGTH, 'rudkobing')
    assert len(slug) == SLUG_LENGTH and slug.endswith('-rudkobing')

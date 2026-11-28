"""A border's node is the border's place, or another: told by name."""
import pytest

from django_osm_locations.utils.same_place import core, differently_written, same_place


@pytest.mark.parametrize('border,node', [
    ('München', 'München'),
    ('Singen (Hohentwiel)', 'Singen'),
    ('Schwarzenbach a.d.Saale', 'Schwarzenbach an der Saale'),
    ('Weidhausen b.Coburg', 'Weidhausen bei Coburg'),
    ('Hochstadt a.Main', 'Hochstadt am Main'),
    ('Weiden in der Oberpfalz', 'Weiden'),
    ('Auerbach/Vogtland', 'Auerbach'),
    ('Oelsnitz/Erzgeb.', 'Oelsnitz'),
    ('Bad Kötzting', 'Kötzting'),
    ('Rothenburg ob der Tauber', 'Rothenburg'),
    ('Villingen-Schwenningen', 'Villingen'),
    ('Châlons-en-Champagne', 'Châlons'),
    ('Łódź', 'Lodz'),
    ('gmina Kraków', 'Kraków'),
])
def test_one_place_under_two_names(border, node):
    assert same_place(border, node)
    assert same_place(node, border)


@pytest.mark.parametrize('border,node', [
    ('Schwentinental', 'Raisdorf'),
    ('Bad Friedrichshall', 'Kochendorf'),
    ('Sankt Peter', 'Sankt Märgen'),
    ('Neustadt', 'Neuhaus'),
    ('München', ''),
    ('München', None),
])
def test_two_places(border, node):
    assert not same_place(border, node)


@pytest.mark.parametrize('name,expected', [
    ('Schwarzenbach a.d.Saale', 'schwarzenbach'),
    ('Schwarzenbach an der Saale', 'schwarzenbach'),
    ('Frankfurt am Main', 'frankfurt'),
    ('Singen (Hohentwiel)', 'singen'),
    ('Am Mellensee', 'am mellensee'),
    ('In der Au', 'in der au'),
    ('St. Peter', 'st peter'),
])
def test_core(name, expected):
    assert core(name) == expected


def test_differently_written():
    assert differently_written('Schwarzenbach a.d.Saale', 'Schwarzenbach an der Saale')
    assert not differently_written('München', 'MÜNCHEN')
    assert not differently_written('München', None)


class TestTheNodeABorderNames:
    """What the importer takes from a municipality's label or seat node."""

    @pytest.fixture
    def importer(self):
        from django.contrib.gis.geos import MultiPolygon, Polygon

        from django_osm_locations.management.commands.importers.base import BaseImporter

        instance = BaseImporter.__new__(BaseImporter)
        instance._is_relation_place = lambda *args: True
        instance._are_all_members_cached = lambda members: True
        instance._within_country = lambda geometry: True
        border = MultiPolygon(Polygon(((0, 0), (10, 0), (10, 10), (0, 10), (0, 0))))
        instance._get_border_from_members = lambda members: border
        instance.already_imported_places_node_ids = set()
        instance.written = []
        instance._upsert = lambda model, key, osm_id, defaults: instance.written.append(defaults)
        return instance

    MEMBERS = [(7, 'n', 'admin_centre')]

    def test_the_towns_point_gives_the_border_its_translations(self, importer):
        importer.nodes = {7: ({'name': 'München', 'name:pl': 'Monachium', 'name:en': 'Munich (node)',
                               'population': '1500000'}, (5.0, 5.0))}
        importer._import_place_from_relation(1, {'name': 'München', 'name:en': 'Munich'}, self.MEMBERS)
        place, = importer.written
        assert place['names'] == {'pl': 'Monachium', 'en': 'Munich'}
        assert place['population'] == '1500000'
        assert importer.already_imported_places_node_ids == {7}

    def test_a_name_written_otherwise_is_kept_beside_the_official_one(self, importer):
        importer.nodes = {7: ({'name': 'Schwarzenbach an der Saale'}, (5.0, 5.0))}
        importer._import_place_from_relation(1, {'name': 'Schwarzenbach a.d.Saale'}, self.MEMBERS)
        place, = importer.written
        assert place['name'] == 'Schwarzenbach a.d.Saale'
        assert place['names'] == {'und': 'Schwarzenbach an der Saale'}
        assert importer.already_imported_places_node_ids == {7}

    def test_the_village_a_municipality_sits_in_stays_a_place(self, importer):
        """Schwentinental's seat is Raisdorf: left out, the village was gone."""
        importer.nodes = {7: ({'name': 'Raisdorf', 'name:da': 'Rådorp', 'population': '7600'}, (5.0, 5.0))}
        importer._import_place_from_relation(1, {'name': 'Schwentinental', 'population': '13000'}, self.MEMBERS)
        place, = importer.written
        assert place['names'] == {}
        assert place['population'] == '13000'
        assert importer.already_imported_places_node_ids == set()

    def test_a_label_without_a_name_is_the_place(self, importer):
        importer.nodes = {7: ({}, (5.0, 5.0))}
        importer._import_place_from_relation(1, {'name': 'Kraków'}, self.MEMBERS)
        assert importer.already_imported_places_node_ids == {7}

"""
The warnings printed when a relation has edges but no usable polygon.

These four branches only run for a boundary whose ways form a multiline that
will not close into a ring, so they stayed unexercised through every earlier
import. They were written as ``print``-style calls -- a message, a name, a URL
-- but ``tqdm.write``'s second positional parameter is ``file``, so the first
relation to reach one of them died with ``'str' object has no attribute
'write'`` partway through importing Poland. Calling them for real, and letting
tqdm write to captured stdout, is what tells us the arguments still line up.
"""
import pytest

from django_osm_locations.management.commands.importers.base import BaseImporter


@pytest.fixture
def importer():
    instance = BaseImporter.__new__(BaseImporter)
    instance._are_all_members_cached = lambda members: True
    # edges that never close into a ring: a multiline, but no polygon
    instance._get_multiline_from_members = lambda members: ['an open multiline']
    instance._get_multiline_from_coords = lambda coords: ['an open multiline']
    instance._get_multipolygon_from_multiline = lambda multiline: None
    return instance


CASES = [
    ('_import_countrysubarea_from_relation', '_is_relation_countrysubarea',
     'CountrySubarea not valid', 'relation'),
    ('_import_place_from_relation', '_is_relation_place',
     'Place not valid', 'relation'),
    ('_import_place_from_way', '_is_way_place',
     'Place not valid', 'way'),
    ('_import_placesubarea_from_relation', '_is_relation_placesubarea',
     'PlaceSubarea not valid', 'relation'),
]


@pytest.mark.parametrize('method,predicate,message,kind', CASES)
def test_warns_with_the_name_and_a_link(importer, capsys, method, predicate, message, kind):
    setattr(importer, predicate, lambda *args: True)

    getattr(importer, method)(4422, {'name': 'Nowhere'}, [])

    printed = capsys.readouterr().out
    assert message in printed
    assert 'Nowhere' in printed
    assert 'http://openstreetmap.org/{}/4422'.format(kind) in printed

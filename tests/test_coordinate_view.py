"""
CoordinateView: (lon, lat) pairs over a flat array.

Way coordinates are stored flat so a country-sized import fits in memory -- a
list of float tuples costs about 167 bytes per pair against 16 packed, which is
1.2GB on the Poland extract. Everything downstream still expects pairs, so the
view has to behave like a sequence of them.
"""
from array import array

import pytest

from django_osm_locations.management.commands.importers.base import CoordinateView


@pytest.fixture
def view():
    return CoordinateView(array('d', [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]))


def test_length_is_pairs_not_floats(view):
    assert len(view) == 3


def test_indexing_returns_pairs(view):
    assert view[0] == (1.0, 2.0)
    assert view[2] == (5.0, 6.0)


def test_negative_indexing(view):
    """_is_way_closed compares refs[0] with refs[-1]."""
    assert view[-1] == (5.0, 6.0)
    assert view[-3] == (1.0, 2.0)


def test_out_of_range(view):
    with pytest.raises(IndexError):
        view[3]


def test_iteration(view):
    assert list(view) == [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]


def test_slicing(view):
    assert view[:2] == [(1.0, 2.0), (3.0, 4.0)]


def test_empty_is_falsey():
    assert not CoordinateView(array('d'))
    assert len(CoordinateView(array('d'))) == 0


def test_non_empty_is_truthy(view):
    assert view


def test_does_not_copy_the_buffer():
    flat = array('d', [1.0, 2.0])
    view = CoordinateView(flat)
    flat.append(3.0); flat.append(4.0)
    assert len(view) == 2, 'the view reads the live buffer'


def test_closed_way_check_works_through_the_view():
    """What _is_way_place relies on."""
    from django_osm_locations.management.commands.importers.base import BaseImporter
    closed = CoordinateView(array('d', [0, 0, 1, 0, 1, 1, 0, 0]))
    open_way = CoordinateView(array('d', [0, 0, 1, 0, 1, 1]))
    assert BaseImporter._is_way_closed(closed) is True
    assert BaseImporter._is_way_closed(open_way) is False

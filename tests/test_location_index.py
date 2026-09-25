"""
Where the node-location index lives during an import.

A 4.8GB France extract grew the editor's cgroup by twelve gigabytes in three
minutes and systemd-oomd took the desktop session down with it -- while the
setting said ``sparse_file_array``. libosmium accepts that bare type name and
maps anonymous memory for it: a file-backed index is only file-backed once it
has been given a file.
"""
import os

import pytest
from django.test import override_settings

from django_osm_locations.management.commands.importers.base import (
    _location_index,
    _location_storage,
)


class TestChoosingTheIndex:
    def test_memory_by_default(self):
        assert _location_storage() == 'flex_mem'

    @override_settings(LOCATIONS_DISK_CACHE=True)
    def test_the_shorthand_selects_a_file_array(self):
        assert _location_storage() == 'sparse_file_array'

    @override_settings(LOCATIONS_LOCATION_STORAGE='dense_mmap_array')
    def test_an_explicit_type_wins(self):
        assert _location_storage() == 'dense_mmap_array'


class TestGivingItAFile:
    """
    The bare type name is the trap: it is accepted, it reads as disk-backed,
    and it is resident.
    """

    @override_settings(LOCATIONS_DISK_CACHE=True)
    def test_a_file_array_is_given_a_file(self, tmp_path):
        with override_settings(LOCATIONS_CACHE_DIR=str(tmp_path)):
            with _location_index() as spec:
                kind, path = spec.split(',', 1)
                assert kind == 'sparse_file_array'
                assert os.path.exists(path)
                assert os.path.dirname(path) == str(tmp_path)

    @override_settings(LOCATIONS_DISK_CACHE=True)
    def test_the_file_does_not_outlive_the_import(self, tmp_path):
        """It is roughly 16 bytes a node -- tens of GB for a large country."""
        with override_settings(LOCATIONS_CACHE_DIR=str(tmp_path)):
            with _location_index() as spec:
                path = spec.split(',', 1)[1]
            assert not os.path.exists(path)

    @override_settings(LOCATIONS_DISK_CACHE=True)
    def test_it_is_cleaned_up_even_when_the_pass_fails(self, tmp_path):
        with override_settings(LOCATIONS_CACHE_DIR=str(tmp_path)):
            with pytest.raises(ValueError):
                with _location_index() as spec:
                    path = spec.split(',', 1)[1]
                    raise ValueError('the extract was truncated')
            assert not os.path.exists(path)

    @override_settings(LOCATIONS_LOCATION_STORAGE='flex_mem')
    def test_an_in_memory_index_is_left_alone(self, tmp_path):
        with override_settings(LOCATIONS_CACHE_DIR=str(tmp_path)):
            with _location_index() as spec:
                assert spec == 'flex_mem'
            assert list(tmp_path.iterdir()) == []

    @override_settings(LOCATIONS_LOCATION_STORAGE='sparse_file_array,/tmp/mine.idx')
    def test_a_path_that_was_given_is_kept(self, tmp_path):
        """Someone naming a path means it, including which filesystem."""
        with override_settings(LOCATIONS_CACHE_DIR=str(tmp_path)):
            with _location_index() as spec:
                assert spec == 'sparse_file_array,/tmp/mine.idx'
            assert list(tmp_path.iterdir()) == []

    @override_settings(LOCATIONS_LOCATION_STORAGE='dense_file_array')
    def test_the_dense_variant_needs_one_too(self, tmp_path):
        with override_settings(LOCATIONS_CACHE_DIR=str(tmp_path)):
            with _location_index() as spec:
                assert spec.startswith('dense_file_array,')

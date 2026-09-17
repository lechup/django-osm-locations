"""
The _presave hook dispatch.

This ran dir(self) plus a getattr over every resulting name on each save, which
was both slow and side-effecting: getattr evaluates properties, so every save
built GEOS geometry it never used.
"""
from django_osm_locations.models import Place, Street


def test_discovers_the_right_hooks_for_each_model():
    assert set(Street._save_hooks()) == {
        '_presave_bbox', '_presave_center', '_presave_length', '_presave_slug',
    }
    assert set(Place._save_hooks()) == {
        '_presave_area', '_presave_bbox', '_presave_center',
        '_presave_population', '_presave_slug',
    }


def test_result_is_cached_per_class():
    first = Street._save_hooks()
    assert Street._save_hooks() is first


def test_cache_is_not_shared_between_models():
    """__dict__ lookup, not attribute lookup -- otherwise subclasses inherit it."""
    assert Street._save_hooks() != Place._save_hooks()


def test_hooks_still_run():
    street = Street(name='Floriańska')
    street._presave()
    assert street.slug == 'florianska'


def test_does_not_evaluate_unrelated_properties():
    """
    Regression: the dir()/getattr() scan forced bbox_polygon, center_bbox and
    center_bbox_polygon on every save, building geometry for nothing.
    """
    evaluated = []

    class Tracking(Place):
        class Meta:
            proxy = True
            app_label = 'locations'

        @property
        def expensive(self):
            evaluated.append('expensive')
            return 1

    obj = Tracking(name='Kraków')
    obj._presave()
    assert evaluated == []


def test_cached_properties_are_not_populated_by_dispatch():
    place = Place(name='Kraków')
    place._presave()
    # a cached_property that was evaluated would be sitting in __dict__
    assert 'bbox_polygon' not in place.__dict__
    assert 'center_bbox_polygon' not in place.__dict__


def test_helper_does_not_match_its_own_filter():
    """
    A helper named with a `_presave_` substring would be discovered as a hook
    and called with the save arguments -- which is exactly what happened.
    """
    for name in Street._save_hooks():
        assert name.startswith('_presave_')
        assert name != '_save_hooks'

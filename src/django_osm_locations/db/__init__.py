import inspect

from django.db.models.expressions import Func
from django.db.models.aggregates import Aggregate
from django.contrib.postgres.aggregates.general import ArrayAgg


class ArrayCat(Func):
    function = 'array_cat'


class MergeMultiline(Aggregate):
    template = "ST_Multi(ST_LineMerge(ST_Multi(ST_union(%(expressions)s))))"


def array_agg_ordered(expression, order_by):
    """
    ``ArrayAgg`` with a defined element order, portable across Django versions.

    Replaces an ``ArrayAgg`` subclass that injected a raw ``ORDER BY ...``
    string through the SQL template. Modern Django resolves ``order_by`` as a
    field reference, so that raised
    ``FieldError: Cannot resolve keyword 'ORDER BY area DESC' into field``
    the moment it ran.

    The kwarg itself was renamed: 4.2 takes ``ordering``, 5.2 added ``order_by``
    and deprecated ``ordering``, and 6.0 removed it. Pick whichever the
    installed version accepts.

    Usage: ``array_agg_ordered('id', '-area')``
    """
    if 'order_by' in inspect.signature(ArrayAgg.__init__).parameters:
        return ArrayAgg(expression, order_by=order_by)
    return ArrayAgg(expression, ordering=order_by)

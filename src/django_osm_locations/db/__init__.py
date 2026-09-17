from django.db.models.expressions import Func
from django.db.models.aggregates import Aggregate
from django.contrib.postgres.aggregates.general import ArrayAgg



class ArrayCat(Func):
    function = 'array_cat'


class MergeMultiline(Aggregate):
    template = "ST_Multi(ST_LineMerge(ST_Multi(ST_union(%(expressions)s))))"


class ArrayAggOrderBy(ArrayAgg):
    """
    Usage: ArrayAggOrderBy('array_field', order_by='ORDER BY area DESC')
    """
    template = '%(function)s(%(expressions)s %(order_by)s)'


class Array(Func):
    """
    Usage: ArrayToString('array_field', delimiter=' ', null_string='')
    """
    function = 'array_to_string'
    template = '%(function)s(%(expressions)s %(order_by)s)'
    arity = 2

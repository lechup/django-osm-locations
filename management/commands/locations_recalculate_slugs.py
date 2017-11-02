# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, F, Func, CharField, Value
from django.db.models.functions import Concat, Coalesce
from tqdm import tqdm

from .. import Country, CountrySubarea, Place, PlaceSubarea, Street, ArrayAggOrderBy


class Command(BaseCommand):
    help = "Recalculate all slugs for any locations objects having the same slug - append id name for all " \
           "objects smaller than the biggest one."

    @classmethod
    def set_default_slugs(cls, model_class):
        model_class.objects.annotate(slugified=Func(F('name'), function='slugify')).update(
            slug=F('slugified')
        )

    @classmethod
    def get_order_by_clause(cls, model_class):
        orderings = []
        for ordering in model_class._meta.ordering:
            order = 'DESC' if '-' in ordering else 'ASC'
            ordering = ordering.replace('-', '')
            orderings.append(
                '{} {}'.format(
                    ordering,
                    order
                )
            )

        return 'ORDER BY {}'.format(
                    ','.join(orderings)
                )

    @classmethod
    def set_unique_slugs(cls, model_class):
        """
        Set slug fields for :param model_class: to unique values by appending object id. First object does not have
        id appended.
        """
        order_by = cls.get_order_by_clause(model_class)
        duplicated_slugs = model_class.objects.values(
            'slug'
        ).annotate(
            slug_count=Count('slug'),
            ids_array=ArrayAggOrderBy(
                'id',
                order_by=order_by
            ),
        ).filter(
            slug_count__gt=1
        ).values_list(
            'slug',
            'ids_array'
        ).order_by()

        for slug, ids_array in tqdm(list(duplicated_slugs), leave=False):
            model_class.objects.filter(
                id__in=ids_array[1:]
            ).annotate(
                new_slug=Concat(
                    'slug',
                    Value('-'),
                    Coalesce('relation_osm_id', 'way_osm_id', 'node_osm_id', 'id'),
                    output_field=CharField()
                )
            ).update(
                slug=F('new_slug')
            )

    def handle(self, *args, **options):
        area_model_classes = [Country, CountrySubarea, Place, PlaceSubarea, Street]
        for area_model_class in area_model_classes:
            print("{} - calculating default slugs...".format(area_model_class.__name__))
            with transaction.atomic():
                self.set_default_slugs(area_model_class)
            print("{} - calculating unique slugs...".format(area_model_class.__name__))
            with transaction.atomic():
                self.set_unique_slugs(area_model_class)

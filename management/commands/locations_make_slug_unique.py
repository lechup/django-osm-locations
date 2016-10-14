# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand
from django.db.models import Count

from .. import Country, CountrySubarea, Place, PlaceSubarea, Street


class Command(BaseCommand):
    help = "Recalculate all slugs for any locations objects having the same slug - append id name for all " \
           "objects smaller than the biggest one."

    def handle(self, *args, **options):
        area_models = [Country, CountrySubarea, Place, PlaceSubarea, Street]
        for area_model in area_models:
            duplicated_slugs = area_model.objects.values('slug').annotate(Count('slug')).filter(
                slug__count__gt=1
            ).order_by()
            for duplicated_slug in duplicated_slugs:
                objects = area_model.objects.filter(slug=duplicated_slug['slug']).iterator()
                # skip first record
                next(objects)
                for object in objects:
                    object.slug = "{}-{}".format(duplicated_slug['slug'], object.id)
                    print("Saving slug for {}.slug = {} ...".format(object, object.slug))
                    object.save(update_fields=['slug'])

# -*- coding: utf-8 -*-
from django.contrib.gis.db.models import MultiLineStringField
from django.contrib.gis.db.models.functions import Area, Length, PointOnSurface
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Cast

from .. import Country, CountrySubarea, Place, PlaceSubarea, Street


class Command(BaseCommand):
    help = "Recalculate all area and length attributes of locations models. Eg. Country.area, Place.area, ect."

    @classmethod
    def set_area(cls, model_class):
        model_class.objects.exclude(border=None).annotate(
            gis_area=Area('gborder')
        ).update(area=F('gis_area') / 1000000)
        model_class.objects.filter(border=None).update(area=0)

    @classmethod
    def set_length(cls, model_class):
        model_class.objects.exclude(lines=None).annotate(
            lines2=Cast('lines', MultiLineStringField(geography=True))
        ).annotate(
            gis_length=Length('lines2')
        ).update(length=F('gis_length'))
        model_class.objects.filter(lines=None).update(length=0)

    @classmethod
    def set_center_for_lines(cls, model_class):
        model_class.objects.exclude(lines=None).annotate(
            new_center=PointOnSurface('lines')
        ).update(center=F('new_center'))

    def handle(self, *args, **options):
        border_model_classes = [Country, CountrySubarea, Place, PlaceSubarea]
        for border_model_class in border_model_classes:
            print("{} - calculating areas...".format(border_model_class.__name__))
            with transaction.atomic():
                self.set_area(border_model_class)

        lines_model_classes = [Street]
        for lines_model_class in lines_model_classes:
            print("{} - calculating lengths...".format(lines_model_class.__name__))
            with transaction.atomic():
                self.set_length(lines_model_class)
            print("{} - calculating centers...".format(lines_model_class.__name__))
            with transaction.atomic():
                self.set_center_for_lines(lines_model_class)

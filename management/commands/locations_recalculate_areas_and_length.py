# -*- coding: utf-8 -*-
from django.contrib.gis.db.models.functions import Area, Length
from django.db.models.functions import Cast
from django.core.management.base import BaseCommand
from django.contrib.gis.db.models import MultiPolygonField, MultiLineStringField

from .. import Country, CountrySubarea, Place, PlaceSubarea, Street


class Command(BaseCommand):
    help = "Recalculate all area and length attributes of locations models. Eg. Country.area, Place.area, ect."

    def handle(self, *args, **options):
        area_models = [Country, CountrySubarea, Place, PlaceSubarea]
        for area_model in area_models:
            objects = area_model.objects.exclude(border=None).annotate(
                border2=Cast('border', MultiPolygonField(geography=True))
            ).annotate(
                gis_area=Area('border2')
            ).all().iterator()
            for object in objects:
                if object.gis_area:
                    object.area = object.gis_area.sq_km
                else:
                    object.area = 0
                print("Saving area for {} = {} sq_km...".format(object, object.area))
                object.save(update_fields=['area'])
            area_model.objects.filter(border=None).update(area=0)

        length_models = [Street]
        for length_model in length_models:
            objects = length_model.objects.exclude(lines=None).annotate(
                lines2=Cast('lines', MultiLineStringField(geography=True))
            ).annotate(gis_length=Length('lines2')).all().iterator()
            for object in objects:
                if object.gis_length:
                    object.length = object.gis_length.m
                else:
                    object.length = 0
                print("Saving length for {} = {} m...".format(object, object.length))
                object.save(update_fields=['length'])
            length_model.objects.filter(lines=None).update(length=0)

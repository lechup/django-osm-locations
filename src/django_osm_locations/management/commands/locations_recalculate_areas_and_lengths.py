from django.contrib.gis.db.models import MultiLineStringField, MultiPolygonField
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
        """
        Area in square kilometres.

        ``Area()`` over a plain geometry column returns the SRID's own units --
        square degrees for 4326 -- so dividing by a million produced a number
        that meant nothing: Warszawa came out as 6.8e-08 rather than 517 km².
        Casting to geography first makes PostGIS compute real square metres,
        which is what the division was always for. ``set_length`` below has
        always done the same cast.
        """
        model_class.objects.exclude(border=None).annotate(
            border_as_geography=Cast('border', MultiPolygonField(geography=True)),
        ).annotate(
            gis_area=Area('border_as_geography')
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
    def set_center_inside_border(cls, model_class):
        """
        Move a centre that has ended up outside what it marks.

        The importer used to take ``center`` from a relation's admin_centre
        node, the seat of the administration, which for a ring-shaped county
        is deliberately outside it: powiat krakowski surrounds Krakow and
        excludes it. The denormalisation asks which borders contain a row's
        centre, so those counties came out as being inside the city they
        encircle -- 102 of them in Poland alone. The seat now has its own
        field; this repairs rows imported before it did.
        """
        outside = model_class.objects.exclude(border=None).exclude(center=None).exclude(
            center__intersects=F('border')
        )
        outside.update(center=PointOnSurface('border'))

    @classmethod
    def set_center_for_lines(cls, model_class):
        model_class.objects.exclude(lines=None).annotate(
            new_center=PointOnSurface('lines')
        ).update(center=F('new_center'))

    def handle(self, *args, **options):
        border_model_classes = [Country, CountrySubarea, Place, PlaceSubarea]
        for border_model_class in border_model_classes:
            print('{} - calculating areas...'.format(border_model_class.__name__).encode('utf-8'))
            with transaction.atomic():
                self.set_area(border_model_class)
            print('{} - moving centres back inside...'.format(
                border_model_class.__name__).encode('utf-8'))
            with transaction.atomic():
                self.set_center_inside_border(border_model_class)

        lines_model_classes = [Street]
        for lines_model_class in lines_model_classes:
            print('{} - calculating lengths...'.format(lines_model_class.__name__).encode('utf-8'))
            with transaction.atomic():
                self.set_length(lines_model_class)
            print('{} - calculating centers...'.format(lines_model_class.__name__).encode('utf-8'))
            with transaction.atomic():
                self.set_center_for_lines(lines_model_class)

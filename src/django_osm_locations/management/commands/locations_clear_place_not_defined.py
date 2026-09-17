from django.core.management.base import BaseCommand

from .. import PlaceSubarea, Street


class Command(BaseCommand):
    help = "Clear all PlaceSubareas and Streets that do not have Place defined (run " \
           "locations_recalculate_denormalized_fields first!)."

    def handle(self, *args, **options):
            mergable_model_classes = [PlaceSubarea, Street]
            for mergable_model_class in mergable_model_classes:
                print("{} - removing objects without Place defined ...".format(mergable_model_class.__name__))
                print(mergable_model_class.objects.filter(place__len=0).delete())

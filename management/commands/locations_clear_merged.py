# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand
from django.db import transaction

from .. import CountrySubarea, PlaceSubarea, Street


class Command(BaseCommand):
    help = "Clear merged locations, can be an issue while doing update."

    def handle(self, *args, **options):
        mergable_model_classes = [CountrySubarea, PlaceSubarea, Street]
        for mergable_model_class in mergable_model_classes:
            print("{} - removing merged objects...".format(mergable_model_class.__name__))
            with transaction.atomic():
                mergable_model_class.objects_all.merged().delete()

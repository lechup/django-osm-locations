# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand
from django.core.management import call_command

from .locations_recalculate_areas_and_lengths import Command as RecalculateAreasAndLengthCommand
from .locations_recalculate_slugs import Command as RecalculateSlugs
from .locations_recalculate_denormalized_fields import Command as RecalculateDenormalizedFields


class Command(BaseCommand):
    help = "Do all recalculations at once:\nlocations_recalculate_areas_and_lengths: {}\n" \
           "locations_recalculate_slugs: {}\nlocations_recalculate_denormalized_fields: {}".format(
               RecalculateAreasAndLengthCommand.help,
               RecalculateSlugs.help,
               RecalculateDenormalizedFields.help
           )

    def handle(self, *args, **options):
        call_command('locations_clear_merged')
        call_command('locations_recalculate_areas_and_lengths')
        call_command('locations_recalculate_slugs')
        call_command('locations_recalculate_denormalized_fields')

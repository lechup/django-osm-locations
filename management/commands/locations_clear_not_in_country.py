# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand

from .importers.base import BaseImporter
from .. import Country, Place, PlaceSubarea, Street


class Command(BaseCommand):
    help = "Clear all locations that are not inside country. Update country border first."

    def add_arguments(self, parser):
        parser.add_argument(
            '-s',
            '--country-slug',
            default=None,
            help='Slug of country You want to clear.',
            type=str,
            required=True
        )
        parser.add_argument(
            '-n',
            '--no-update-country-border',
            default=False,
            action='store_true',
            help='Do not update country border based on CountrySubarea borders.',
        )

    def handle(self, *args, **options):
        for country in Country.objects.filter(slug=options['country_slug']):
            if not options['no_update_country_border']:
                BaseImporter.do_update_country_border(country.slug)
                country.refresh_from_db()
            mergable_model_classes = [Place, PlaceSubarea, Street]
            for mergable_model_class in mergable_model_classes:
                print("{} - removing objects outside of country borders...".format(mergable_model_class.__name__))
                print(mergable_model_class.objects.exclude(center__within=country.border).only('id').delete())

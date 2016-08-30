# -*- coding: utf-8 -*-
import os
from optparse import make_option

from django.contrib.gis.utils import LayerMapping
from django.core.management.base import BaseCommand

from .. import Country


class Command(BaseCommand):
    data_file_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', 'data',
                     'TM_WORLD_BORDERS-0.3.shp')
    )
    data_mapping = {
        'name': 'NAME',
        'iso2': 'ISO2',
        'iso3': 'ISO3',
        'un': 'UN',
        'border': 'MULTIPOLYGON',
    }
    option_list = BaseCommand.option_list + (
        make_option(
            '--file', '-f',
            action='store_true',
            dest='data_file_path',
            default=data_file_path,
            help='Path to world borders shapefile'
        ),
    )
    help = "Importing countries with borders from '%s'" % data_file_path

    def handle(self, *args, **options):
        lm = LayerMapping(Country, options['data_file_path'], self.data_mapping,
                          transform=False, encoding='iso-8859-1')
        print 'Starting importing countries...'
        lm.save(verbose=True)
        print 'Importing countries done! In case of any errors probably you have already run this command, haven\'t' \
              ' you? :>'

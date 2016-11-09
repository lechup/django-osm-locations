# -*- coding: utf-8 -*-
import importlib

import os

import requests

from django.core.management.base import BaseCommand
from django.core.management import call_command

from .. import Country


class Command(BaseCommand):
    data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'data'))
    osm_repository = 'http://download.geofabrik.de/'
    osm_repository_file_name = '%(country)s-latest.osm.pbf'
    osm_repository_mapping = {
        'albania': 'europe/',
        'poland': 'europe/',
        'isle-of-man': 'europe/',
    }
    osm_repository_url = '%s%%(country_path)s%%(file_name)s' % (osm_repository,)

    help = "Way of importing locations data. We are looking for files in locations/data/ directory. " \
           "If data file does not exist for any country, we dowload it from %s repository. See " \
           "optional arguments for details." % osm_repository

    def add_arguments(self, parser):
        parser.add_argument(
            '-r',
            '--repo',
            default=self.osm_repository,
            help='Change default OSM repo (%s) to provided one.' % self.osm_repository
        )
        parser.add_argument(
            '-u',
            '--update',
            action='store_true',
            default=False,
            help='Update OSM location structure from default %s or defined by --repo . Default = False.' %
                 self.osm_repository
        )
        parser.add_argument(
            '-i',
            '--import-only',
            action='store_true',
            default=False,
            help='Make import only, do not run merge. We do import and merge by default. See --merge-only.'
        )
        parser.add_argument(
            '-m',
            '--merge-only',
            action='store_true',
            default=False,
            help='Make merge only, do not run import. We do import and merge by default. See --import-only.'
        )
        parser.add_argument(
            'country',
            nargs='+'
        )

    def _get_osm_file_name(self, country):
        return self.osm_repository_file_name % {
            'country': country
        }

    def _get_osm_file_path(self, country):
        return os.path.join(
            self.data_dir,
            self._get_osm_file_name(country)
        )

    def _get_osm_url(self, country):
        return self.osm_repository_url % {
            'country_path': self.osm_repository_mapping[country],
            'file_name': self._get_osm_file_name(country)
        }

    def _download_osm_file(self, country):
        osm_file = self._get_osm_file_path(country)
        osm_url = self._get_osm_url(country)

        r = requests.head(osm_url)
        if 'content-length' in r.headers:
            osm_file_size = '%.02f MB' % (float(r.headers['content-length']) / (1024 * 1024),)
        else:
            osm_file_size = 'unknown size'

        print("Starting downloading %s (%s) to %s. It may take a while!" % (osm_url, osm_file_size, osm_file))

        r = requests.get(osm_url, stream=True)
        with open(osm_file, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024):
                # filter out keep-alive new chunks
                if chunk:
                    f.write(chunk)
                    f.flush()
        print("Download successful!")
        return True

    def handle(self, *args, **options):
        self.osm_repository = options['repo']

        if len(options['country']) == 0:
            exit("Provide at least one country name!")
        else:
            if options['merge_only'] and options['import_only']:
                exit("Set one option: --merge-only or --import-only, or remove both. By default we do import + merge.")

            # populate countries
            if Country.objects.count() == 0:
                call_command('loaddata', 'locations_country.json')

            # for each country from arguments ...
            for country in options['country']:
                country = country.lower()
                # check if we have mapping for it
                if country not in self.osm_repository_mapping:
                    print("'%s' is not known country..." % country)
                    continue

                # download country data if --update, -u or data does not exists
                if not os.path.exists(self._get_osm_file_path(country)) or options['update']:
                    self._download_osm_file(country)

                # dynamic import of specific country importer
                importer_module = importlib.import_module(self._get_module_name(country), self._get_package_name())
                importer_class = getattr(importer_module, self._get_class_name(country))
                importer = importer_class(country, self._get_osm_file_path(country))

                if (not options['merge_only'] and not options['import_only']) or options['import_only']:
                    importer.do_unmerge()
                    importer.do_import()
                if (not options['merge_only'] and not options['import_only']) or options['merge_only']:
                    importer.do_merge()

    @staticmethod
    def _get_class_name(country):
        return ''.join([x.title() for x in country.split('-')]) + 'Importer'

    @staticmethod
    def _get_module_name(country):
        return '.importers.%s' % country.replace('-', '_')

    @staticmethod
    def _get_package_name():
        package_path = __name__.split('.')
        return '.'.join(package_path[:-1])

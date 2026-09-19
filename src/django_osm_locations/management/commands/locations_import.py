import importlib
import os

from django.core.management.base import BaseCommand, CommandError

from .. import Country
from ...conf import get_data_dir


class Command(BaseCommand):
    osm_repository = 'http://download.geofabrik.de/'
    osm_repository_file_name = '%(country)s-latest.osm.pbf'
    osm_repository_country_to_geofabric_path_mapping = {
        'poland': 'europe/',
        'germany': 'europe/',
        'isle-of-man': 'europe/',
        'denmark': 'europe/',
        'finland': 'europe/',
        'norway': 'europe/',
        'netherlands': 'europe/',
    }
    osm_repository_country_to_slug_mapping = {
        'poland': 'polska',
        'germany': 'deutschland',
        'isle-of-man': 'isle-of-man',
        'denmark': 'danmark',
        'finland': 'suomi',
        'norway': 'norge',
        'netherlands': 'nederland',
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
            help='Update OSM location structure from default %s or defined by --repo . Default = False.' %
                 self.osm_repository
        )
        parser.add_argument(
            '-i',
            '--import-only',
            action='store_true',
            help='Make import only, do not run merge. We do import and merge by default. See --merge-only.'
        )
        parser.add_argument(
            '-m',
            '--merge-only',
            action='store_true',
            help='Make merge only, do not run import. We do import and merge by default. See --import-only.'
        )
        parser.add_argument(
            '--no-progress',
            action='store_true',
            help='Never draw progress bars. They are drawn only on an '
                 'interactive terminal anyway, so this is for forcing them off '
                 'when one is attached.',
        )
        parser.add_argument(
            'country',
            nargs='+'
        )

    def _get_osm_file_name(self, country):
        return self.osm_repository_file_name % {
            'country': country
        }

    @property
    def data_dir(self):
        data_dir = get_data_dir()
        os.makedirs(data_dir, exist_ok=True)
        return data_dir

    def _get_osm_file_path(self, country):
        return os.path.join(
            self.data_dir,
            self._get_osm_file_name(country)
        )

    def _get_osm_url(self, country):
        return self.osm_repository_url % {
            'country_path': self.osm_repository_country_to_geofabric_path_mapping[country],
            'file_name': self._get_osm_file_name(country)
        }

    def _download_osm_file(self, country):
        import requests  # optional: only needed when actually downloading an extract

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

    @classmethod
    def is_country_defined(cls, country):
        is_country_gofabric_mapping = country in cls.osm_repository_country_to_geofabric_path_mapping
        is_country_slug_mapping = country in cls.osm_repository_country_to_slug_mapping
        return is_country_gofabric_mapping and is_country_slug_mapping

    def handle(self, *args, **options):
        self.osm_repository = options['repo']

        if len(options['country']) == 0:
            exit("Provide at least one country name!")
        else:
            if options['merge_only'] and options['import_only']:
                exit("Set one option: --merge-only or --import-only, or remove both. By default we do import + merge.")

            countries = [name.lower() for name in options['country']]

            # Refuse the whole run rather than skipping quietly: a typo used to
            # print a line and exit 0, so `make import norwya` reported success
            # having done nothing.
            unknown = [name for name in countries if not self.is_country_defined(name)]
            if unknown:
                raise CommandError(
                    "Unknown country: {}. Known countries: {}".format(
                        ', '.join(unknown),
                        ', '.join(sorted(self.osm_repository_country_to_slug_mapping)),
                    )
                )

            # Check the Country rows exist before downloading anything, since an
            # extract can be gigabytes and the failure would otherwise arrive
            # after it, as a bare DoesNotExist.
            missing = [
                name for name in countries
                if not Country.objects.filter(
                    slug=self.osm_repository_country_to_slug_mapping[name]
                ).exists()
            ]
            if missing:
                raise CommandError(
                    "No Country row for: {}. They are created by migration "
                    "0031/0032, so run: manage.py migrate".format(', '.join(missing))
                )

            for osm_repository_country in countries:
                is_osm_file_downloaded = os.path.exists(
                    self._get_osm_file_path(osm_repository_country)
                )

                # download country data if --update, -u or data does not exists
                if not is_osm_file_downloaded or options['update']:
                    self._download_osm_file(osm_repository_country)

                # dynamic import of specific country importer
                importer_module = importlib.import_module(
                    self._get_module_name(osm_repository_country),
                    self._get_package_name()
                )
                importer_class = getattr(importer_module, self._get_class_name(osm_repository_country))
                country_slug = self.osm_repository_country_to_slug_mapping[osm_repository_country]
                importer = importer_class(
                    country_slug,
                    self._get_osm_file_path(osm_repository_country),
                    show_progress=self.show_progress(options),
                )

                if (not options['merge_only'] and not options['import_only']) or options['import_only']:
                    importer.do_import()
                if (not options['merge_only'] and not options['import_only']) or options['merge_only']:
                    importer.do_merge()

    @staticmethod
    def show_progress(options):
        """False when asked to be quiet, otherwise leave it to detection."""
        if options.get('no_progress') or not options.get('verbosity', 1):
            return False
        return None

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

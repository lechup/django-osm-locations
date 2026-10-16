import os

from django.core.management.base import BaseCommand, CommandError

from .. import Country
from ...conf import get_data_dir
from ...utils.extracts import cut_extract, is_stale, merge_extracts
from ...utils.query_log import stop_logging_queries
from .importers import get_importer, known_countries


class Command(BaseCommand):
    osm_repository = 'http://download.geofabrik.de/'
    osm_repository_file_name = '%(country)s-latest.osm.pbf'
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

    @staticmethod
    def _extract_name(country):
        """The Geofabrik extract a country is read from: San Marino's is Italy's."""
        return get_importer(country).geofabrik_name

    def _get_osm_file_path(self, country):
        return os.path.join(
            self.data_dir,
            self._get_osm_file_name(self._extract_name(country))
        )

    def _get_osm_url(self, country):
        return self.osm_repository_url % {
            'country_path': get_importer(country).geofabrik_path,
            'file_name': self._get_osm_file_name(self._extract_name(country))
        }

    def _download_osm_file(self, country):
        return self._download(self._get_osm_url(country), self._get_osm_file_path(country))

    def _extra_extracts(self, country):
        """``(url, local path)`` of every further extract the country needs."""
        found = []
        for extra in get_importer(country).extra_extracts:
            path, _, name = extra.rpartition('/')
            file_name = self._get_osm_file_name(name)
            url = self.osm_repository_url % {
                'country_path': path + '/' if path else '', 'file_name': file_name}
            found.append((url, os.path.join(self.data_dir, file_name)))
        return found

    def _merged_osm_file_path(self, country):
        return os.path.join(self.data_dir, '%s-merged-latest.osm.pbf' % country)

    def _osm_file_to_read(self, country, update=False):
        """
        The extract the importer reads: the country's own, or -- when parts of
        it are in other extracts -- all of them merged into one file.
        """
        main = self._get_osm_file_path(country)
        bbox = get_importer(country).extract_bbox
        if bbox:
            cut = os.path.join(self.data_dir, '%s-cut-latest.osm.pbf' % country)
            if is_stale(cut, [main]):
                print("Cutting %s out of %s ..." % (country, os.path.basename(main)))
                written = cut_extract(main, bbox, cut)
                print("Cut: %d objects.\n" % written)
            return cut
        extras = self._extra_extracts(country)
        if not extras:
            return main
        for url, path in extras:
            if not os.path.exists(path) or update:
                self._download(url, path)
        sources = [main] + [path for _, path in extras]
        merged = self._merged_osm_file_path(country)
        if is_stale(merged, sources):
            print("Merging %s into %s ..." % (', '.join(os.path.basename(p) for p in sources), merged))
            written = merge_extracts(sources, merged)
            print("Merged: %d objects.\n" % written)
        return merged

    def _download(self, osm_url, osm_file):
        import requests  # optional: only needed when actually downloading an extract

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
        return get_importer(country) is not None

    def handle(self, *args, **options):
        self.osm_repository = options['repo']

        # Before anything reads or writes: with DEBUG on, Django's query log
        # grows by 0.6MB per batch of streets and would reach 4.9GB.
        stop_logging_queries(self.stdout)

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
                        ', '.join(unknown), ', '.join(known_countries()),
                    )
                )

            # Make sure the country exists before downloading anything: an
            # extract can be gigabytes, and finding out afterwards through a
            # bare DoesNotExist is an expensive way to be told. The row comes
            # from what the importer declares, so a new country needs no
            # migration.
            for name in countries:
                self.ensure_country(get_importer(name))

            for osm_repository_country in countries:
                is_osm_file_downloaded = os.path.exists(
                    self._get_osm_file_path(osm_repository_country)
                )

                # download country data if --update, -u or data does not exists
                if not is_osm_file_downloaded or options['update']:
                    self._download_osm_file(osm_repository_country)

                importer_class = get_importer(osm_repository_country)
                importer = importer_class(
                    importer_class.country_slug,
                    self._osm_file_to_read(osm_repository_country, options['update']),
                    show_progress=self.show_progress(options),
                )

                if (not options['merge_only'] and not options['import_only']) or options['import_only']:
                    importer.do_import()
                if (not options['merge_only'] and not options['import_only']) or options['merge_only']:
                    importer.do_merge()

    @staticmethod
    def ensure_country(importer_class):
        """
        Create the Country row from what the importer declares about itself.

        Matched on the uniqueness constraint, so a row seeded by a migration or
        loaded from the fixture is reused rather than duplicated.
        """
        Country.objects.get_or_create(
            iso2=importer_class.iso2,
            iso3=importer_class.iso3,
            un=importer_class.un,
            defaults={
                'name': importer_class.country_name,
                'slug': importer_class.country_slug,
            },
        )

    @staticmethod
    def show_progress(options):
        """False when asked to be quiet, otherwise leave it to detection."""
        if options.get('no_progress') or not options.get('verbosity', 1):
            return False
        return None

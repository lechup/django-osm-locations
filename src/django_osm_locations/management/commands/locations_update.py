import importlib
import os

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from .locations_import import Command as ImportCommand


def _load_replication():
    try:
        from osmium.replication import utils
        from osmium.replication.server import ReplicationServer
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            "Updating from OSM diffs needs pyosmium. "
            "Install it with: pip install 'django-osm-locations[importers]'"
        ) from exc
    return utils, ReplicationServer


class Command(BaseCommand):
    """
    Bring everything up to date from OSM change files.

    A ``.pbf`` extract is a snapshot, not a changelog: re-downloading the whole
    file is the only way to refresh it from the extract alone, and even then a
    feature deleted upstream just quietly disappears from the file. The
    replication server the extract came from publishes the diffs instead, and
    the extract's own header records which sequence it was cut at, so an update
    only has to fetch what changed since.
    """

    help = (
        "Update an existing extract from the OSM replication server, import the "
        "changes into PostGIS and rebuild the search index. Downloads only the "
        "diffs since the extract was last updated, not the whole file."
    )

    def add_arguments(self, parser):
        parser.add_argument('country', nargs='+')
        parser.add_argument(
            '-n', '--dry-run',
            action='store_true',
            help='Report how far behind each extract is without downloading '
                 'anything or touching the database.',
        )
        parser.add_argument(
            '--max-size',
            type=int,
            default=102400,
            help='Maximum diff data to buffer, in kB. Default 102400 (100MB). '
                 'An extract far behind is caught up over several runs.',
        )
        parser.add_argument(
            '--no-progress',
            action='store_true',
            help='Never draw progress bars.',
        )
        parser.add_argument(
            '--no-recalculate',
            action='store_true',
            help='Skip recalculating slugs, ancestry and derived geometry.',
        )
        parser.add_argument(
            '--no-reindex',
            action='store_true',
            help='Skip rebuilding the search index. The database is still '
                 'updated; the index keeps serving the previous data.',
        )

    def handle(self, *args, **options):
        utils, ReplicationServer = _load_replication()
        importer_command = ImportCommand()

        updated = []
        for country in options['country']:
            country = country.lower()
            if not ImportCommand.is_country_defined(country):
                raise CommandError("'{}' is not a known country".format(country))

            path = importer_command._get_osm_file_path(country)
            if not os.path.exists(path):
                raise CommandError(
                    "No extract at {}. Run locations_import {} first.".format(path, country)
                )

            header = utils.get_replication_header(path)
            if not header.url or header.sequence is None:
                raise CommandError(
                    "{} carries no replication header, so there is nothing to "
                    "update from. Re-download it with: locations_import "
                    "--update {}".format(path, country)
                )

            self.stdout.write('{}: at sequence {} ({})'.format(
                country, header.sequence, header.timestamp
            ))

            server = ReplicationServer(header.url)
            try:
                state = server.get_state_info()
                if state is not None:
                    self.stdout.write('  server is at sequence {} ({} behind)'.format(
                        state.sequence, state.sequence - header.sequence
                    ))
                    if state.sequence <= header.sequence:
                        self.stdout.write('  already up to date')
                        continue

                if options['dry_run']:
                    self.stdout.write('  would download diffs and reimport')
                    continue

                # libosmium picks the output format from the extension, so the
                # temporary name has to keep a recognisable one
                outfile = '{}.updating.osm.pbf'.format(path)
                if os.path.exists(outfile):
                    os.remove(outfile)

                self.stdout.write('  downloading diffs from {}'.format(header.url))
                result = server.apply_diffs_to_file(
                    path, outfile, header.sequence + 1,
                    max_size=options['max_size'],
                    outformat='pbf',
                )
            finally:
                server.close()

            if result is None:
                self.stdout.write('  no diffs applied')
                if os.path.exists(outfile):
                    os.remove(outfile)
                continue

            sequence = result[0] if isinstance(result, (tuple, list)) else result
            # replace only once the new file is complete
            os.replace(outfile, path)
            self.stdout.write('  extract now at sequence {}'.format(sequence))

            self._reimport(country, path, show_progress=ImportCommand.show_progress(options))
            updated.append(country)

        if not updated or options['dry_run']:
            return

        if not options['no_recalculate']:
            self.stdout.write('Recalculating derived fields ...')
            call_command('locations_recalculate_all')

        if not options['no_reindex']:
            self.stdout.write('Rebuilding the search index ...')
            call_command('locations_reindex')

    def _reimport(self, country, path, show_progress=None):
        """
        Run the country's importer over the updated extract.

        The import upserts on OSM id, so re-running it over a newer extract
        updates the rows that changed and adds the ones that appeared. Nothing
        is deleted -- neither here nor by the merge step, which only flags
        duplicates.
        """
        self.stdout.write('  importing {} ...'.format(os.path.basename(path)))
        module = importlib.import_module(
            ImportCommand._get_module_name(country),
            ImportCommand._get_package_name(),
        )
        importer_class = getattr(module, ImportCommand._get_class_name(country))
        slug = ImportCommand.osm_repository_country_to_slug_mapping[country]
        importer = importer_class(slug, path, show_progress=show_progress)
        importer.do_import()
        importer.do_merge()

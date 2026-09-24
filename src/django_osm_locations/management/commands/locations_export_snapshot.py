"""
Package a country's live indexes for publication.

What stands between a fresh checkout and a working search today is about
thirteen hours: importing an extract, clipping borders to the coastline,
recalculating slugs and ancestry, and indexing. A published snapshot removes
all of it for anyone who only wants to search.
"""
from django.core.management.base import BaseCommand, CommandError

from ...search.client import get_client, get_index_prefix
from ...search.snapshots import (
    DEFAULT_REPOSITORY,
    DEFAULT_REPOSITORY_PATH,
    create,
    indexes_for,
    register_repository,
)


class Command(BaseCommand):
    help = (
        "Snapshot the live indexes for one country, ready to publish. The "
        "cluster must have been started with path.repo set; see "
        "docker-compose.local.yml."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'country', nargs='?', default=None,
            help='ISO 3166-1 alpha-2 code of the country to package, lowercased '
                 '-- pl, gb, dk. Omit to snapshot everything the aliases point '
                 'at, which is what a single-country deployment wants.',
        )
        parser.add_argument(
            '--name', default=None,
            help='Snapshot name. Defaults to the country and the date, which '
                 'is what a consumer needs to see: how old the data is, not '
                 'when the snapshot was taken.',
        )
        parser.add_argument(
            '--repository', default=DEFAULT_REPOSITORY,
            help='Repository to write into. Default {}.'.format(DEFAULT_REPOSITORY),
        )
        parser.add_argument(
            '--location', default=DEFAULT_REPOSITORY_PATH,
            help='Directory the cluster writes snapshots to, as the cluster '
                 'sees it. Must match path.repo. Default {}.'.format(
                     DEFAULT_REPOSITORY_PATH),
        )

    @staticmethod
    def default_name(country):
        from datetime import date
        return '{}-{}'.format(country or 'all', date.today().isoformat())

    def handle(self, *args, **options):
        client = get_client()
        prefix = get_index_prefix()
        country = (options.get('country') or '').lower() or None

        indexes = indexes_for(country, prefix=prefix, client=client)
        if not indexes:
            raise CommandError(
                'Nothing is aliased for {}. Index it before packaging it.'.format(
                    country or 'any country'))

        try:
            register_repository(
                client=client, name=options['repository'],
                location=options['location'],
            )
        except RuntimeError as error:
            raise CommandError(str(error))

        name = options['name'] or self.default_name(country)
        self.stdout.write('packaging {} index(es) as {}'.format(len(indexes), name))
        for index in indexes:
            self.stdout.write('  {}'.format(index))

        def report(state, done, total):
            self.stdout.write('  {} {}/{} shards'.format(state, done, total))

        state = create(
            name, indexes, client=client, repository=options['repository'],
            on_progress=report if options.get('verbosity', 1) > 1 else None,
        )
        if state == 'PARTIAL':
            raise CommandError(
                'Snapshot {} came out PARTIAL: some shards did not make it. '
                'OpenSearch does not treat that as an error, so it would '
                'publish as though it were whole.'.format(name))

        self.stdout.write(
            'written to {} on the cluster. Tar that directory, split it under '
            'the 2GB an asset may be, and publish the parts with their '
            'checksums.'.format(options['location'])
        )

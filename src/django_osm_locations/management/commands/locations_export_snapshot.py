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
    DEFAULT_REPOSITORY_PATH,
    create,
    describe,
    indexes_for,
    merge,
    pack_metadata,
    register_repository,
    repository_for,
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
            '--root', default=DEFAULT_REPOSITORY_PATH,
            help='Directory the cluster writes snapshots under, as the cluster '
                 'sees it. Must match path.repo. Each country is written to a '
                 'directory of its own beneath it, which is what makes one '
                 'country a download of its own. Default {}.'.format(
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

        repository, location = repository_for(country, root=options['root'])
        try:
            register_repository(client=client, name=repository, location=location)
        except RuntimeError as error:
            raise CommandError(str(error))

        name = options['name'] or self.default_name(country)
        self.stdout.write('packaging {} index(es) as {}'.format(len(indexes), name))
        for index in indexes:
            self.stdout.write('  {}'.format(index))

        # what is published is scored by its own term statistics: no
        # document written again may linger in them, deleted
        self.stdout.write('merging each into one segment')
        try:
            merge(indexes, client=client)
        except RuntimeError as error:
            raise CommandError(str(error))

        def report(state, done, total):
            self.stdout.write('  {} {}/{} shards'.format(state, done, total))

        # what built it travels inside it, for the loader to check
        metadata = pack_metadata()
        state = create(
            name, indexes, client=client, repository=repository,
            on_progress=report if options.get('verbosity', 1) > 1 else None,
            metadata=metadata,
        )
        if state == 'PARTIAL':
            raise CommandError(
                'Snapshot {} came out PARTIAL: some shards did not make it. '
                'OpenSearch does not treat that as an error, so it would '
                'publish as though it were whole.'.format(name))

        self.stdout.write('  {}'.format(describe(metadata)))
        self.stdout.write(
            'written to {} on the cluster. That directory is the unit of '
            'publication: `make pack {}` tars it, splits it under '
            'the 2GB a release asset may be, and writes the checksums.'.format(
                location, name)
        )

from django.core.management.base import BaseCommand, CommandError

from ...search.indexer import new_index_suffix, reindexers


class Command(BaseCommand):
    help = (
        "Rebuild the search indexes from PostGIS. Builds a new index per location "
        "type, checks its document count against the database, then swaps the "
        "alias over. The previously live index is kept so the swap can be undone."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'types',
            nargs='*',
            help='Location types to rebuild. Defaults to all of them.',
        )
        parser.add_argument(
            '--country',
            default=None,
            help='Rebuild only this country, named as the importer is: '
                 'poland, united-kingdom. Its index is kept apart from the '
                 'others and the alias covers them all, so countries can be '
                 'built, published and replaced one at a time.',
        )
        parser.add_argument(
            '-n', '--dry-run',
            action='store_true',
            help='Report what would be rebuilt, and how it compares to what is '
                 'live, without writing anything.',
        )
        parser.add_argument(
            '--no-promote',
            action='store_true',
            help='Build and populate the new index but leave the alias alone. '
                 'Use to inspect the result before switching traffic to it.',
        )
        parser.add_argument(
            '--tolerance',
            type=int,
            default=0,
            help='How many documents the new index may be short of the database '
                 'row count before the swap is refused. Default 0.',
        )
        parser.add_argument(
            '--no-progress',
            action='store_true',
            help='Never draw progress bars. They are drawn only on an '
                 'interactive terminal anyway, so this is for forcing them off '
                 'when one is attached.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Documents per bulk request. Default 500.',
        )

    @staticmethod
    def _resolve_country(name):
        """
        The Country row for an importer's name, or None for everything.

        Named the way the importers are -- poland, united-kingdom -- because
        that is the vocabulary the rest of the commands use.
        """
        if not name:
            return None
        from ...models import Country
        from .importers import registry

        importers = registry()
        importer = importers.get(name)
        if importer is None:
            raise CommandError('Unknown country: {}. Known: {}.'.format(
                name, ', '.join(sorted(importers))))
        try:
            return Country.objects.get(slug=importer.country_slug)
        except Country.DoesNotExist:
            raise CommandError(
                'No row for {} yet; import it before indexing it.'.format(name))

    @staticmethod
    def _show_progress(options):
        """False when asked to be quiet, otherwise leave it to detection."""
        if options.get('no_progress') or not options.get('verbosity', 1):
            return False
        return None

    def handle(self, *args, **options):
        # one suffix for the whole run, so a rebuild is identifiable as a set
        suffix = new_index_suffix()
        selected = options['types'] or None
        country = self._resolve_country(options.get('country'))
        try:
            targets = reindexers(
                selected,
                suffix=suffix,
                country=country,
                batch_size=options['batch_size'],
                show_progress=self._show_progress(options),
            )
        except ValueError as exc:
            raise CommandError(str(exc))

        if options['dry_run']:
            return self._report(targets)

        failures = []
        for reindexer in targets:
            name = reindexer.index.name
            expected = reindexer.database_count()
            self.stdout.write('{}: {} rows in the database'.format(name, expected))

            reindexer.create()
            self.stdout.write('  building {}'.format(reindexer.index_name))
            reindexer.populate(total=expected)

            actual = reindexer.indexed_count()
            self.stdout.write('  indexed {} of {}'.format(actual, expected))

            shortfall = expected - actual
            if shortfall > options['tolerance']:
                failures.append(
                    '{}: indexed {} of {} rows ({} missing, tolerance {})'.format(
                        name, actual, expected, shortfall, options['tolerance']
                    )
                )
                self.stderr.write(
                    '  NOT promoting {}: {} documents short'.format(
                        reindexer.index_name, shortfall
                    )
                )
                continue

            if options['no_promote']:
                self.stdout.write('  built but not promoted (--no-promote)')
                continue

            replaced = reindexer.promote()
            self.stdout.write('  alias {} -> {}'.format(
                reindexer.alias, reindexer.index_name
            ))
            for old in replaced:
                if old != reindexer.index_name:
                    self.stdout.write('  previous index kept: {}'.format(old))

        if failures:
            raise CommandError(
                'Reindex incomplete:\n  ' + '\n  '.join(failures)
            )

    def _report(self, targets):
        for reindexer in targets:
            database = reindexer.database_count()
            live = reindexer.live_count()
            self.stdout.write(
                '{}: {} rows in the database, {} currently indexed ({:+d})'.format(
                    reindexer.index.name, database, live, database - live
                )
            )
            self.stdout.write('  would build {}'.format(reindexer.index_name))

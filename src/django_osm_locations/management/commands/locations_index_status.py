from django.core.management.base import BaseCommand

from ...search.indexer import reindexers


class Command(BaseCommand):
    help = (
        "Compare what is in PostGIS with what is currently served by the search "
        "aliases. Read-only: it changes neither the database nor the index."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'types',
            nargs='*',
            help='Location types to report on. Defaults to all of them.',
        )

    def handle(self, *args, **options):
        rows = []
        for reindexer in reindexers(options['types'] or None):
            database = reindexer.database_count()
            live = reindexer.live_count()
            rows.append((reindexer.index.name, database, live, database - live))

        width = max(len(row[0]) for row in rows)
        header = '{:<{w}}  {:>10}  {:>10}  {:>10}'.format(
            'type', 'database', 'indexed', 'difference', w=width
        )
        self.stdout.write(header)
        self.stdout.write('-' * len(header))
        for name, database, live, difference in rows:
            self.stdout.write('{:<{w}}  {:>10}  {:>10}  {:>+10}'.format(
                name, database, live, difference, w=width
            ))

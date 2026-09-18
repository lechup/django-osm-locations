from django.core.management.base import BaseCommand

from ...search.indexer import reindexers


class Command(BaseCommand):
    help = (
        "Remove old search indexes that no alias points at. Never touches the "
        "database, and never removes an index that is currently serving traffic."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'types',
            nargs='*',
            help='Location types to clean up. Defaults to all of them.',
        )
        parser.add_argument(
            '-k', '--keep',
            type=int,
            default=1,
            help='How many superseded indexes to keep per type, newest first, '
                 'as a rollback path. Default 1.',
        )
        parser.add_argument(
            '-n', '--dry-run',
            action='store_true',
            help='List what would be removed without removing it.',
        )

    def handle(self, *args, **options):
        for reindexer in reindexers(options['types'] or None):
            live = set(reindexer.aliased_indexes())
            superseded = [
                name for name in reindexer.existing_indexes() if name not in live
            ]
            # newest last, so keep the tail
            removable = superseded[:-options['keep']] if options['keep'] else superseded

            self.stdout.write('{}: {} live, {} superseded, {} removable'.format(
                reindexer.index.name, len(live), len(superseded), len(removable)
            ))
            for name in removable:
                if options['dry_run']:
                    self.stdout.write('  would remove {}'.format(name))
                else:
                    reindexer.client.indices.delete(index=name)
                    self.stdout.write('  removed {}'.format(name))

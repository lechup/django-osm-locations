"""
Load a published index, and put it where the API can see it.

The restore itself is one API call. What this adds is the two things that call
does not do, either of which leaves a consumer with a working index and an
empty API: aliases are not restored, and a second country must join the first
rather than replace it.
"""
from django.core.management.base import BaseCommand, CommandError

from ...search.client import get_client, get_index_prefix
from ...search.snapshots import (
    DEFAULT_REPOSITORY,
    DEFAULT_REPOSITORY_PATH,
    point_aliases,
    register_repository,
    restore,
)


class Command(BaseCommand):
    help = (
        "Restore a published snapshot and point the aliases at it. Several "
        "countries can be loaded one after another; each joins the aliases "
        "rather than replacing what is already there."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'snapshot',
            help='Name of the snapshot in the repository, e.g. pl-2026-09-24.',
        )
        parser.add_argument(
            '--repository', default=DEFAULT_REPOSITORY,
            help='Repository to read from. Default {}.'.format(DEFAULT_REPOSITORY),
        )
        parser.add_argument(
            '--location', default=DEFAULT_REPOSITORY_PATH,
            help='Directory the cluster reads snapshots from, as the cluster '
                 'sees it. Must match path.repo. Default {}.'.format(
                     DEFAULT_REPOSITORY_PATH),
        )
        parser.add_argument(
            '--list', action='store_true', dest='list_only',
            help='List what the repository holds and exit.',
        )

    def handle(self, *args, **options):
        client = get_client()
        prefix = get_index_prefix()

        try:
            register_repository(
                client=client, name=options['repository'],
                location=options['location'],
            )
        except RuntimeError as error:
            raise CommandError(str(error))

        if options['list_only']:
            return self._list(client, options['repository'])

        name = options['snapshot']
        indexes = self._indexes_in(client, options['repository'], name)
        if not indexes:
            raise CommandError('Snapshot {} holds no indexes.'.format(name))

        clashes = [i for i in indexes if client.indices.exists(index=i)]
        if clashes:
            raise CommandError(
                'Already loaded: {}. Restoring would fail on them; delete them '
                'first if you mean to replace them.'.format(', '.join(clashes)))

        self.stdout.write('restoring {} index(es) from {}'.format(len(indexes), name))
        restore(name, client=client, repository=options['repository'])

        aliased = point_aliases(indexes, client=client, prefix=prefix)
        for index in indexes:
            self.stdout.write('  {}'.format(index))
        self.stdout.write('aliases now serving: {}'.format(', '.join(sorted(set(aliased)))))

    def _list(self, client, repository):
        found = client.snapshot.get(repository=repository, snapshot='_all')
        for snapshot in found.get('snapshots', []):
            self.stdout.write('{}  {} index(es)  {}'.format(
                snapshot.get('snapshot'),
                len(snapshot.get('indices') or []),
                snapshot.get('state', ''),
            ))

    @staticmethod
    def _indexes_in(client, repository, name):
        try:
            found = client.snapshot.get(repository=repository, snapshot=name)
        except Exception as error:
            raise CommandError('No snapshot {} in {}: {}'.format(name, repository, error))
        snapshots = found.get('snapshots') or []
        if not snapshots:
            return []
        return sorted(snapshots[0].get('indices') or [])

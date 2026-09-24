"""
Load a published index, and put it where the API can see it.

The restore itself is one API call. What this adds is the three things that
call does not do, any of which leaves a consumer with a working index and an
empty API: the repository has to be found first, aliases are not restored, and
a second country must join the first rather than replace it.
"""
from django.core.management.base import BaseCommand, CommandError

from ...search.client import get_client, get_index_prefix
from ...search.snapshots import (
    DEFAULT_REPOSITORY,
    DEFAULT_REPOSITORY_PATH,
    point_aliases,
    register_repository,
    repository_for,
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
            'snapshot', nargs='?', default=None,
            help='Name of the snapshot in the repository, e.g. pl-2026-09-24. '
                 'Not needed with --list.',
        )
        parser.add_argument(
            '--in', dest='directory', default=None,
            help='Directory the snapshot was extracted into, named relative to '
                 'the snapshots root -- pl, gb, all. Defaults to the first '
                 'part of the snapshot name, which is what publishing produces.',
        )
        parser.add_argument(
            '--root', default=DEFAULT_REPOSITORY_PATH,
            help='Directory the cluster reads snapshots under, as the cluster '
                 'sees it. Must match path.repo. Default {}.'.format(
                     DEFAULT_REPOSITORY_PATH),
        )
        parser.add_argument(
            '--list', action='store_true', dest='list_only',
            help='List what is loadable. With --in, what that directory holds; '
                 'without it, every repository already registered.',
        )

    @staticmethod
    def directory_for(options):
        """
        Which directory to read, from what the caller said or the name implies.

        A published snapshot is named `<country>-<date>` and lives in a
        directory named for that country, so the name gives the directory
        away. It is a convention rather than a rule, which is why `--in` is
        there: someone who extracted the tarball elsewhere is not stuck.
        """
        if options.get('directory'):
            return options['directory']
        name = options.get('snapshot') or ''
        return name.split('-')[0] or None

    def handle(self, *args, **options):
        client = get_client()
        prefix = get_index_prefix()

        directory = self.directory_for(options)
        if options['list_only'] and not directory:
            return self._list_registered(client)

        if not directory:
            raise CommandError(
                'Which snapshot? Pass its name, or --list to see what is '
                'already registered.')

        repository, location = repository_for(directory, root=options['root'])
        try:
            register_repository(client=client, name=repository, location=location)
        except RuntimeError as error:
            raise CommandError(str(error))

        if options['list_only']:
            return self._list(client, repository, location)

        name = options['snapshot']
        indexes = self._indexes_in(client, repository, location, name)
        if not indexes:
            raise CommandError('Snapshot {} holds no indexes.'.format(name))

        clashes = [i for i in indexes if client.indices.exists(index=i)]
        if clashes:
            raise CommandError(
                'Already loaded: {}. Restoring would fail on them; delete them '
                'first if you mean to replace them.'.format(', '.join(clashes)))

        self.stdout.write('restoring {} index(es) from {}'.format(len(indexes), name))
        restore(name, client=client, repository=repository)

        aliased = point_aliases(indexes, client=client, prefix=prefix)
        for index in indexes:
            self.stdout.write('  {}'.format(index))
        self.stdout.write('aliases now serving: {}'.format(', '.join(sorted(set(aliased)))))

    def _list_registered(self, client):
        """
        What the cluster already knows about.

        Nothing on a fresh machine, which is the common case and worth saying
        plainly: a consumer who has just unpacked a download has a directory,
        not a repository, and no API can find it for them.
        """
        found = client.snapshot.get_repository() or {}
        ours = sorted(n for n in found if n.startswith(DEFAULT_REPOSITORY))
        if not ours:
            return self.stdout.write(
                'No snapshot repository registered yet. Say which directory to '
                'look in: --list --in pl, naming the directory you unpacked.')
        for repository in ours:
            self._list(client, repository,
                       (found[repository].get('settings') or {}).get('location', ''))

    def _list(self, client, repository, location):
        found = client.snapshot.get(repository=repository, snapshot='_all')
        snapshots = found.get('snapshots') or []
        if not snapshots:
            return self.stdout.write('{}: empty'.format(location or repository))
        self.stdout.write('{}:'.format(location or repository))
        for snapshot in snapshots:
            self.stdout.write('  {}  {} index(es)  {}'.format(
                snapshot.get('snapshot'),
                len(snapshot.get('indices') or []),
                snapshot.get('state', ''),
            ))

    @staticmethod
    def _indexes_in(client, repository, location, name):
        try:
            found = client.snapshot.get(repository=repository, snapshot=name)
        except Exception as error:
            raise CommandError(
                'No snapshot {} in {}: {}. If you unpacked it somewhere else, '
                'pass --in <directory>.'.format(name, location, error))
        snapshots = found.get('snapshots') or []
        if not snapshots:
            return []
        return sorted(snapshots[0].get('indices') or [])

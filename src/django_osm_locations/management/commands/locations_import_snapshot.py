"""
Load a published index, and put it where the API can see it.

The restore itself is one API call. What this adds is the four things that
call does not do, any of which leaves a consumer with a working index and an
empty or failing API: the repository has to be found first, the restore has
to be waited for -- the call returns long before the shards are back --
aliases are not restored, and a second country must join the first rather
than replace it.
"""
import re

from django.core.management.base import BaseCommand, CommandError

from ... import __version__
from ...search.client import get_client, get_index_prefix
from ...search.snapshots import (
    DEFAULT_REPOSITORY,
    DEFAULT_REPOSITORY_PATH,
    describe,
    forget_repository,
    point_aliases,
    register_repository,
    repository_for,
    restore,
    retire_older,
    version_tuple,
    wait_for_restore,
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
            '--group', default=None,
            help='Load every part of a country imported a part at a time: '
                 '--group us restores each unpacked us-* directory -- us-ca, '
                 'us-tx, and us, the country itself -- and they search as one.',
        )
        parser.add_argument(
            '--host-root', default='local/snapshots',
            help='Where those directories are on this machine, for --group: '
                 'the directory the cluster sees as --root. Default '
                 'local/snapshots, as docker-compose.local.yml mounts it.',
        )
        parser.add_argument(
            '--ignore-version', action='store_true',
            help='Load a pack even though it says it needs a newer version of '
                 'this package than the one installed. What an older reader '
                 'makes of a newer index is not known.',
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
        # everything before the date: pl-2026-09-24 is in pl, a state's
        # us-ca-2026-10-18 in us-ca
        return re.sub(r'-\d{4}-\d{2}-\d{2}$', '', name) or None

    @staticmethod
    def group_directories(group, host_root):
        """The unpacked parts of a group under ``host_root``: ``us`` and every ``us-*``."""
        import os

        if not os.path.isdir(host_root):
            return []
        return sorted(
            name for name in os.listdir(host_root)
            if (name == group or name.startswith(group + '-'))
            and os.path.isdir(os.path.join(host_root, name))
        )

    DATED = re.compile(r'-\d{4}-\d{2}-\d{2}$')

    def handle(self, *args, **options):
        client = get_client()
        prefix = get_index_prefix()

        if options['list_only']:
            directory = self.directory_for(options)
            if not directory:
                return self._list_registered(client)
            repository, location = self._register(client, directory, options)
            return self._list(client, repository, location)

        target = options.get('group') or options.get('snapshot')
        if not target:
            raise CommandError(
                'Which snapshot? Pass its name, or --list to see what is '
                'already registered.')

        # A dated name is that version. An undated one is the newest of it:
        # pl is Poland's latest pack, and us -- which has no directory of its
        # own but parts, us-ca, us-tx -- the latest of every part.
        if self.DATED.search(target) and not options.get('group'):
            return self._load(client, prefix, self.directory_for(options), target, options)
        if options.get('directory'):
            return self._load(client, prefix, options['directory'], None, options)
        directories = self.group_directories(target, options['host_root'])
        if not directories:
            # not unpacked here, or unpacked where only the cluster can see
            directories = [target]
        for directory in directories:
            self._load(client, prefix, directory, None, options)

    def _register(self, client, directory, options):
        repository, location = repository_for(directory, root=options['root'])
        # what is in the directory now, not what the cluster last saw there:
        # a pack unpacked over it since is read through that memory as empty
        forget_repository(client=client, name=repository)
        try:
            register_repository(client=client, name=repository, location=location)
        except RuntimeError as error:
            raise CommandError(str(error))
        return repository, location

    def _load(self, client, prefix, directory, name, options):
        """
        Restore one snapshot -- the given one, or the newest in the directory
        -- and put it under the aliases in place of an older version of the
        same part, if one is loaded: two versions of a state under one alias
        would answer every search twice.
        """
        repository, location = self._register(client, directory, options)
        if name is None:
            found = client.snapshot.get(repository=repository, snapshot='_all')
            names = sorted(s['snapshot'] for s in found.get('snapshots') or [])
            if not names:
                self.stdout.write('{}: nothing to load{}'.format(
                    location, self._why_empty(directory, options)))
                return
            name = names[-1]
        snapshot = self._snapshot(client, repository, location, name)
        indexes = sorted(snapshot.get('indices') or [])
        if not indexes:
            raise CommandError('Snapshot {} holds no indexes.'.format(name))
        self._check_version(name, snapshot.get('metadata') or {}, options)
        if all(client.indices.exists(index=i) for i in indexes):
            self.stdout.write('{}: already loaded'.format(name))
            return
        clashes = [i for i in indexes if client.indices.exists(index=i)]
        if clashes:
            raise CommandError(
                'Partly loaded: {}. Delete those indexes first.'.format(', '.join(clashes)))

        self.stdout.write('restoring {} index(es) from {}'.format(len(indexes), name))
        restore(name, client=client, repository=repository)
        # Nothing goes under an alias, and nothing older is retired, until
        # every primary is active: until then what is loaded keeps answering.
        try:
            wait_for_restore(indexes, client=client, on_progress=self._progress())
        except RuntimeError as error:
            raise CommandError(
                '{} did not finish restoring, so nothing was put under the aliases and '
                'whatever was loaded before still answers: {}. Delete {} and load it '
                'again.'.format(name, error, ', '.join(indexes)))
        aliased = point_aliases(indexes, client=client, prefix=prefix)
        for index in indexes:
            self.stdout.write('  {}'.format(index))
        for index in retire_older(indexes, client=client, prefix=prefix):
            self.stdout.write('  replaces {}'.format(index))
        self.stdout.write('aliases now serving: {}'.format(', '.join(sorted(set(aliased)))))

    #: Seconds between progress lines while a restore is copied back.
    PROGRESS_EVERY = 60

    def _progress(self):
        """A reporter for wait_for_restore: a line a minute, not one a poll."""
        import time

        last = [None]

        def report(active, total, recovered, size):
            now = time.monotonic()
            if last[0] is not None and now - last[0] < self.PROGRESS_EVERY:
                return
            last[0] = now
            copied = ', {:.0%} of {:.1f} GB copied so far'.format(
                recovered / size, size / 1e9) if size else ''
            self.stdout.write('  {} of {} shard(s) active{}'.format(active, total, copied))
        return report

    @staticmethod
    def _why_empty(directory, options):
        """
        Why a directory holding a snapshot lists none, when it does.

        The repository's manifest is its highest index-N. A pack unpacked over
        a directory that once held a newer, emptied one -- its snapshot
        deleted after packing -- brings its own index-N back beside that one,
        and the newer, empty manifest is the one read.
        """
        import glob
        import os

        here = os.path.join(options['host_root'], directory)
        if not glob.glob(os.path.join(here, 'snap-*.dat')):
            return ''
        return (' -- yet {} holds snapshot files: an emptier index-N from an '
                'older repository there is read first. Remove the directory and '
                'unpack the pack again.'.format(here))

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
            self.stdout.write('  {}  {} index(es)  {}  {}'.format(
                snapshot.get('snapshot'),
                len(snapshot.get('indices') or []),
                snapshot.get('state', ''),
                describe(snapshot.get('metadata')),
            ))

    @staticmethod
    def _snapshot(client, repository, location, name):
        try:
            found = client.snapshot.get(repository=repository, snapshot=name)
        except Exception as error:
            raise CommandError(
                'No snapshot {} in {}: {}. If you unpacked it somewhere else, '
                'pass --in <directory>.'.format(name, location, error))
        return (found.get('snapshots') or [{}])[0]

    def _check_version(self, name, metadata, options):
        """
        Say what built the pack, and refuse one that needs a newer reader.

        A pack made before packs carried this says nothing, and loads as it
        always did: every one of them loads with 0.2.0 or later.
        """
        self.stdout.write('{}: {}'.format(name, describe(metadata)))
        needs = metadata.get('loads_with')
        if not needs or version_tuple(needs) <= version_tuple(__version__):
            return
        if options.get('ignore_version'):
            self.stdout.write('  loading it anyway with {} (--ignore-version)'.format(__version__))
            return
        raise CommandError(
            '{} needs django-osm-locations {} or later to load; this is {}. Upgrade, '
            'or pass --ignore-version to load it regardless.'.format(name, needs, __version__))

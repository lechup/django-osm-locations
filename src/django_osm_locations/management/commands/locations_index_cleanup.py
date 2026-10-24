"""
Listing and removing old search indexes.

Every rebuild leaves the index it replaced in place as a rollback path, so
indexes pile up: a few weeks of rebuilding Poland and Denmark left 40 of them,
over 20GB. This sorts them out per location type and per country -- Poland's
rollback is Poland's previous index, not whichever name happens to sort last --
and removes only when asked to.

Each index gets one of four statuses:

* live      -- an alias points at it; never removed
* pending   -- newer than the live index of its group and not yet promoted, a
               ``locations_reindex --no-promote`` build awaiting inspection;
               never removed
* rollback  -- among the newest ``--keep`` superseded indexes of its group
* stale     -- older than that; what ``--delete`` removes

A group is one location type in one country, or the type across all countries
for indexes built before per-country rebuilds. An index whose name does not
parse is shown as unknown and left alone.

``--rollback --country pl`` puts Poland's newest rollback index back under the
alias in place of its live one, for every type in one atomic alias update.
Running it again steps back further. The index taken off is not removed: it is
now newer than the live one, so it shows as pending, and ``--forward`` puts the
newest pending index back -- undoing a rollback, or promoting a
``locations_reindex --no-promote`` build.
"""
import re

from django.core.management.base import BaseCommand, CommandError

from ...search.indexer import reindexers

LIVE, PENDING, ROLLBACK, STALE, UNKNOWN = 'live', 'pending', 'rollback', 'stale', 'unknown'

#: What follows ``<prefix>-<type>-``: an optional country token, then the stamp.
# a country's code, or a state's: us-ca
TAIL = re.compile(r'^(?:(?P<country>[a-z]{2}(?:-[a-z]{2})?)-)?(?P<stamp>\d{14})$')

#: The group of indexes built for every country at once.
ALL = 'all'


def parse(name, stem):
    """(country, stamp) from an index name, or None when it does not parse."""
    if not name.startswith(stem):
        return None
    match = TAIL.match(name[len(stem):])
    if not match:
        return None
    return match.group('country') or ALL, match.group('stamp')


def plan(names, live, stem, keep):
    """
    The status of every index of one location type.

    Returns (name, country, stamp, status) tuples sorted by country, then
    oldest first. Pure: the command feeds it from OpenSearch, tests directly.
    """
    live = set(live)
    groups, rows = {}, []
    for name in names:
        parsed = parse(name, stem)
        if parsed is None:
            rows.append((name, '?', '', UNKNOWN))
        else:
            groups.setdefault(parsed[0], []).append((parsed[1], name))

    for country, members in groups.items():
        members.sort()
        live_stamps = [stamp for stamp, name in members if name in live]
        newest_live = max(live_stamps) if live_stamps else None
        superseded = [
            (stamp, name) for stamp, name in members
            if name not in live and (newest_live is None or stamp < newest_live)
        ]
        kept = set(name for _, name in superseded[-keep:]) if keep else set()
        for stamp, name in members:
            if name in live:
                status = LIVE
            elif newest_live is not None and stamp > newest_live:
                status = PENDING
            elif name in kept:
                status = ROLLBACK
            else:
                status = STALE
            rows.append((name, country, stamp, status))

    rows.sort(key=lambda row: (row[1] != ALL, row[1], row[2]))
    return rows


def swap(rows, forward=False):
    """
    (live names, replacement name) for one group's plan rows, or None.

    Back: the newest index older than the live one. Forward: the newest index
    newer than it, which plan() marks pending.
    """
    live = [row[0] for row in rows if row[3] == LIVE]
    wanted = (PENDING,) if forward else (ROLLBACK, STALE)
    candidates = [row for row in rows if row[3] in wanted]
    if not live or not candidates:
        return None
    return live, max(candidates, key=lambda row: row[2])[0]


class Command(BaseCommand):
    help = (
        "List search indexes, or remove the superseded ones no alias points at. "
        "Only reports unless --delete is given. Never touches the database, and "
        "never removes an index that is serving traffic."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'types',
            nargs='*',
            help='Location types to look at. Defaults to all of them.',
        )
        parser.add_argument(
            '-l', '--list',
            action='store_true',
            help='Show every index with its size and status, and remove nothing.',
        )
        parser.add_argument(
            '-k', '--keep',
            type=int,
            default=1,
            help='How many superseded indexes to keep per type and country, '
                 'newest first, as a rollback path. Default 1.',
        )
        parser.add_argument(
            '-c', '--country',
            help="Only this country's indexes, by alpha-2 code (pl, dk), or "
                 "'all' for those built for every country at once.",
        )
        parser.add_argument(
            '--delete',
            action='store_true',
            help='Remove the stale indexes. Without it, only reports what would go.',
        )
        parser.add_argument(
            '--rollback',
            action='store_true',
            help="Put a country's previous indexes back under the aliases in "
                 "place of the live ones. Needs --country. Only reports unless "
                 "--apply is given.",
        )
        parser.add_argument(
            '--forward',
            action='store_true',
            help="The opposite of --rollback: put a country's newest pending "
                 "index under the aliases. Needs --country.",
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Perform the --rollback or --forward.',
        )
        parser.add_argument(
            '-n', '--dry-run',
            action='store_true',
            help='Report only. The default; kept so older invocations still work.',
        )

    def handle(self, *args, **options):
        if options['keep'] < 0:
            raise CommandError('--keep cannot be negative')
        if options['delete'] and (options['dry_run'] or options['list']):
            raise CommandError('--delete cannot be combined with --dry-run or --list')
        moving = options['rollback'] or options['forward']
        if options['rollback'] and options['forward']:
            raise CommandError('--rollback or --forward, not both')
        if options['apply'] and not moving:
            raise CommandError('--apply goes with --rollback or --forward')
        if moving and (options['delete'] or options['list']):
            raise CommandError('--rollback and --forward cannot be combined '
                               'with --delete or --list')
        country = (options['country'] or '').lower() or None
        if moving and not country:
            raise CommandError(
                '--rollback and --forward need --country (pl, dk, or all): '
                'switching every country at once is rarely what is meant')
        try:
            selected = reindexers(options['types'] or None, show_progress=False)
        except ValueError as exc:
            raise CommandError(str(exc))
        if moving:
            return self._switch(selected, country, options['forward'], options['apply'])

        removing = options['delete']
        totals = {LIVE: 0, PENDING: 0, ROLLBACK: 0, STALE: 0, UNKNOWN: 0}
        freed = 0
        for reindexer in selected:
            stem = '{}-{}-'.format(reindexer.prefix, reindexer.index.name)
            rows = plan(reindexer.existing_indexes(), reindexer.aliased_indexes(),
                        stem, options['keep'])
            if country:
                rows = [row for row in rows if row[1] == country]
            if not rows:
                continue
            sizes = self._sizes(reindexer)

            self.stdout.write(self.style.MIGRATE_HEADING(reindexer.index.name))
            for name, _, _, status in rows:
                totals[status] += 1
                docs, size = sizes.get(name, ('?', 0))
                line = '  {:<9} {:>9}  {:>12} docs  {}'.format(
                    status, _human(size), docs, name)
                if status == STALE and not options['list']:
                    freed += size
                    if removing:
                        reindexer.client.indices.delete(index=name)
                        line = '  {:<9} {:>9}  {:>12} docs  {}'.format(
                            'removed', _human(size), docs, name)
                    else:
                        line = self.style.WARNING(line)
                self.stdout.write(line)

        self.stdout.write('')
        self.stdout.write('{live} live, {pending} pending, {rollback} rollback, '
                          '{stale} stale, {unknown} unknown'.format(**totals))
        if options['list']:
            return
        if not totals[STALE]:
            self.stdout.write('nothing to remove')
        elif removing:
            self.stdout.write(self.style.SUCCESS(
                'removed {} indexes, {} freed'.format(totals[STALE], _human(freed))))
        else:
            self.stdout.write(
                'would remove {} indexes, {}. Nothing was removed: add --delete '
                '(make os-index remove delete) to remove them.'.format(
                    totals[STALE], _human(freed)))

    def _switch(self, selected, country, forward, apply):
        verb = 'forward' if forward else 'rollback'
        actions = []
        for reindexer in selected:
            stem = '{}-{}-'.format(reindexer.prefix, reindexer.index.name)
            rows = [row for row in plan(reindexer.existing_indexes(),
                                        reindexer.aliased_indexes(), stem, 1)
                    if row[1] == country]
            found = swap(rows, forward)
            self.stdout.write(self.style.MIGRATE_HEADING(reindexer.index.name))
            if found is None:
                self.stdout.write('  nothing to switch to')
                continue
            live, target = found
            for name in live:
                self.stdout.write('  off  {}'.format(name))
                actions.append({'remove': {'index': name, 'alias': reindexer.alias}})
            self.stdout.write('  on   {}'.format(target))
            actions.append({'add': {'index': target, 'alias': reindexer.alias}})

        self.stdout.write('')
        if not actions:
            self.stdout.write('nothing to switch')
        elif apply:
            # one call, so search never sees one type switched and another not
            selected[0].client.indices.update_aliases(body={'actions': actions})
            self.stdout.write(self.style.SUCCESS('{} of {} done{}'.format(
                verb, country,
                '' if forward else '; make os-index forward apply ISO={} undoes it'.format(
                    country))))
        else:
            self.stdout.write(
                'Nothing was changed: add --apply (make os-index {} apply '
                'ISO={}) to switch.'.format(verb, country))

    @staticmethod
    def _sizes(reindexer):
        """
        {name: (documents, bytes)}; empty when the cluster will not say.

        Documents from _count, not from _cat's docs.count: that one counts
        nested documents too, and shows 50 million for Poland's 6.9 million
        addresses.
        """
        pattern = reindexer.index.index_name(reindexer.prefix, '*')
        try:
            rows = reindexer.client.cat.indices(
                index=pattern, format='json', bytes='b', h='index,store.size')
        except Exception:  # noqa: BLE001 - sizes are a courtesy, not the point
            return {}
        sizes = {}
        for row in rows:
            try:
                docs = reindexer.client.count(index=row['index'])['count']
            except Exception:  # noqa: BLE001
                docs = '?'
            sizes[row['index']] = (docs, int(row.get('store.size') or 0))
        return sizes


def _human(size):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return '{:.1f}{}'.format(size, unit) if unit != 'B' else '{}B'.format(size)
        size /= 1024.0

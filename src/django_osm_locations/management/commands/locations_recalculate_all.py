from contextlib import contextmanager
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.db import connection

from .locations_recalculate_areas_and_lengths import Command as RecalculateAreasAndLengthCommand
from .locations_recalculate_slugs import Command as RecalculateSlugs
from .locations_recalculate_denormalized_fields import Command as RecalculateDenormalizedFields
from .. import Address, Country, CountrySubarea, Place, PlaceSubarea, Street
from ...utils.recalc_scope import RecalcScope


class Command(BaseCommand):
    """
    Note: this used to begin by deleting every row flagged as merged. It no
    longer removes anything -- PostGIS is the source of truth for the search
    index, and merged rows are simply left out of the index instead.
    """

    help = "Do all recalculations at once:\nlocations_recalculate_areas_and_lengths: {}\n" \
           "locations_recalculate_slugs: {}\nlocations_recalculate_denormalized_fields: {}".format(
               RecalculateAreasAndLengthCommand.help,
               RecalculateSlugs.help,
               RecalculateDenormalizedFields.help
           )

    #: Each ancestor, paired with the level whose slugs its slugs qualify.
    #: Walked in order, so an ancestor's slug is settled before it is copied
    #: into anything below it.
    LEVELS = (
        ('Country', 'CountrySubarea'),
        ('CountrySubarea', 'Place'),
        ('Place', 'PlaceSubarea'),
        ('PlaceSubarea', 'Street'),
    )

    #: (label, duration) per phase, so the end can say where the time went.
    timings = None

    @contextmanager
    def timed(self, label):
        """
        Announce a phase, and say how long it took.

        Four hours of recalculation died on its first slug pass and the log
        could not say how those four hours had been spent -- there was nothing
        to tell a slow phase from a stuck one, or to decide what a tuning
        change had achieved. Each phase now prints when it starts and what it
        cost, and the totals are repeated at the end.
        """
        self.labels_seen.append(label)
        if self.start_from and not self.reached:
            if label != self.start_from:
                self.stdout.write('skipping {}'.format(label))
                yield False
                return
            self.reached = True

        start = datetime.now()
        self.stdout.write('{} - {}...'.format(start.strftime('%H:%M:%S'), label))
        try:
            yield True
        finally:
            took = datetime.now() - start
            self.timings.append((label, took))
            self.stdout.write('  {} took {}'.format(label, took))

    def add_arguments(self, parser):
        parser.add_argument(
            '--country', default=None,
            help='Recalculate one country only, named as the importer is: '
                 'poland, united-kingdom. The rest of the database is left '
                 'exactly as it is -- slugs are unique per country, so adding '
                 'or re-importing one changes nothing in another, and without '
                 'this every country was recalculated every time.',
        )
        parser.add_argument(
            '--from', dest='start_from', default=None, metavar='PHASE',
            help='Skip every phase before PHASE and run from there -- the '
                 'label as the log prints it, e.g. "streets without a place". '
                 'For resuming after a phase was killed: each phase commits '
                 'on its own, so the ones before it are already in the '
                 'database. The skipping happens on the same code path as a '
                 'full run, so the order cannot drift from it.',
        )
        parser.add_argument(
            '--one-pass', action='store_true', default=False,
            help='Push each ancestor level down in one statement instead of one '
                 'per ancestor row. Measured on Place -> Address: 27,993 '
                 'statements at 1.88s each extrapolates to 14h36m, which is why '
                 'the option exists. Passed straight through to '
                 'locations_recalculate_denormalized_fields.',
        )

    def handle(self, *args, **options):
        """
        Areas first, then slugs and ancestry walked down the hierarchy.

        Slugs and ancestry each need the other: a duplicate slug is qualified
        by an ancestor's *slug* (``_ancestry_qualifier`` returns ``pair[1]``),
        and the denormalised arrays are where that slug is stored. Run slugs
        first and a freshly imported country has empty arrays, so every
        duplicate falls back to its OSM id -- ``station-road-25986049``, the
        form the slug command exists to avoid. Run the two as whole passes in
        either order and one of them has to run twice, because the slug pass
        changes ancestors' slugs after they have been copied downwards.

        The dependency is hierarchical rather than circular, though: a street
        needs its place's slug, a place needs its country subarea's. Walking
        top-down settles each level's slugs before pushing them down, so every
        ancestor is written exactly once. The arrays are cleaned once around
        the whole walk rather than per level.
        """
        self.timings = []
        self.labels_seen = []
        self.start_from = options.get('start_from')
        self.reached = False
        started = datetime.now()

        country = self._resolve_country(options.get('country'))
        if country is not None:
            if options.get('one_pass'):
                raise CommandError(
                    '--one-pass cannot be combined with --country: it has not '
                    'been taught the scope, and would push every country down.')
            self.stdout.write('scoping the recalculation to {}...'.format(country.name))
            RecalcScope.build(country, self.SCOPED_MODELS, report=self.stdout.write)
        try:
            self._walk(options)
        finally:
            if country is not None:
                RecalcScope.clear()

        if self.start_from and not self.reached:
            raise CommandError('No phase called {!r}. The phases are: {}.'.format(
                self.start_from, ', '.join(repr(l) for l in self.labels_seen)))

        self.report_write_amplification()
        self.report_timings(started)

    #: What a scoped run restricts itself to. Country is handled apart: its own
    #: slugs stay global, and the walk pushes down only the one country.
    SCOPED_MODELS = (CountrySubarea, Place, PlaceSubarea, Street, Address)

    @staticmethod
    def _resolve_country(name):
        """The Country row for an importer's name, the way locations_reindex does it."""
        if not name:
            return None
        from .importers import registry

        importers = registry()
        importer = importers.get(name)
        if importer is None:
            raise CommandError('Unknown country: {}. Known: {}.'.format(
                name, ', '.join(sorted(importers))))
        try:
            return Country.objects.get(slug=importer.country_slug)
        except Country.DoesNotExist:
            raise CommandError('No row for {} yet; import it first.'.format(name))

    def _walk(self, options):
        with self.timed('areas and lengths') as run:
            if run:
                call_command('locations_recalculate_areas_and_lengths')

        with self.timed('cleaning denorm fields') as run:
            if run:
                RecalculateDenormalizedFields.clean_denorm_fields_before_update()
        # Countries have nothing to be qualified by, so their slugs are
        # already final and the walk can start by pushing them down.
        with self.timed('slugs: Country') as run:
            if run:
                call_command('locations_recalculate_slugs', 'Country')
        for ancestor, descendant in self.LEVELS:
            with self.timed('ancestry: {} down'.format(ancestor)) as run:
                if run:
                    call_command(
                        'locations_recalculate_denormalized_fields', ancestor,
                        no_clean_before=True, no_clean_after=True, no_search_vector=True,
                        one_pass=options.get('one_pass', False),
                    )
            if ancestor == 'Place':
                # Streets inside no place border take the nearest one, and it
                # has to happen here: the slug pass qualifies a duplicate by
                # its place, so running this after the walk -- as it first did
                # -- left 771,321 streets slugged with nothing to qualify by,
                # and the OSM-id fallback rate unmoved at 27.7%.
                with self.timed('streets without a place') as run:
                    if run:
                        RecalculateDenormalizedFields.update_street_place_field()
                # Immediately after, because the street's place is final here
                # and the place's own ancestry was settled by the two levels
                # above. Two containment passes over 2.8 million streets
                # become one join.
                with self.timed('streets inherit from their place') as run:
                    if run:
                        RecalculateDenormalizedFields.inherit_street_ancestry_from_place()
            with self.timed('slugs: {}'.format(descendant)) as run:
                if run:
                    call_command('locations_recalculate_slugs', descendant)
        # After the walk, because it copies each place's finished ancestry and
        # final slug onto the addresses inside it.
        with self.timed('addresses inherit from their place') as run:
            if run:
                RecalculateDenormalizedFields.inherit_address_ancestry_from_place()

        # After the streets are slugged, because an address stores its
        # street's slug, and by name rather than by containment -- an address
        # says which street it is on, and a point is not inside a line.
        with self.timed('addresses to streets') as run:
            if run:
                RecalculateDenormalizedFields.update_address_street_field()
        # After the street link, which places most of the addresses no place
        # contains; this takes the rest by border and the nearest place.
        with self.timed('addresses outside every place') as run:
            if run:
                RecalculateDenormalizedFields.place_addresses_outside_places()
        # Last, because an address's slug reads its parent -- its street, or
        # failing that its place -- from element [1] of those arrays, and both
        # are only final once the steps above have run. There used to be a
        # pass here stripping a placeholder out of every array first; the walk
        # no longer writes one, so there is nothing to strip.
        with self.timed('address slugs') as run:
            if run:
                RecalculateDenormalizedFields.update_address_slugs()

        # The tsvector columns are not rebuilt here. Search moved to
        # OpenSearch, where an edge n-gram analyser does the prefix matching
        # they used to -- search/indexes.py says as much -- and nothing in the
        # package queries them any more. Building them cost about two hours of
        # every recalculation, most of it one UPDATE over 2.8 million streets.
        # The columns are left in place for anything downstream still reading
        # them; run locations_recalculate_denormalized_fields to refresh them.

    def report_timings(self, started):
        total = datetime.now() - started
        self.stdout.write('\nwhere the time went:')
        for label, took in sorted(self.timings, key=lambda pair: -pair[1]):
            share = 100 * took.total_seconds() / max(total.total_seconds(), 1)
            self.stdout.write('  {:>12}  {:5.1f}%  {}'.format(
                str(took).split('.')[0], share, label))
        self.stdout.write('  {:>12}         total'.format(str(total).split('.')[0]))

    def report_write_amplification(self):
        """
        What the pass cost in writes, which is what it is actually made of.

        Every ancestor pushed down rewrites its descendants, and a rewrite is a
        new row version. The numbers worth comparing between runs are the
        rewrites per row -- above 1 means a level rewrote the same rows more
        than once -- and the share of them PostgreSQL could keep on the page as
        HOT, which is the share that did not touch any of the table's indexes.

        Read from pg_stat_user_tables, so it measures since the counters were
        last reset. Reset them before a run that is meant to be compared:
        SELECT pg_stat_reset();
        """
        # Reporting has no business failing the command it reports on, and a
        # caller without database access -- a test exercising the order of the
        # walk, for instance -- is not an error.
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT relname, n_live_tup, n_tup_upd, n_tup_hot_upd,
                           pg_size_pretty(pg_total_relation_size(relid))
                    FROM pg_stat_user_tables
                    WHERE relname LIKE 'locations_%' AND n_tup_upd > 0
                    ORDER BY n_tup_upd DESC
                """)
                rows = cursor.fetchall()
        except Exception:
            return

        if not rows:
            return
        self.stdout.write('\nwrites, since the counters were last reset:')
        self.stdout.write('  {:<26} {:>12} {:>14} {:>9} {:>8} {:>9}'.format(
            'table', 'rows', 'rewrites', 'per row', 'HOT', 'size'))
        for name, live, updated, hot, size in rows:
            self.stdout.write('  {:<26} {:>12,} {:>14,} {:>9.2f} {:>7.0f}% {:>9}'.format(
                name, live, updated, updated / max(live, 1),
                100 * hot / max(updated, 1), size))

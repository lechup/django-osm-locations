from contextlib import contextmanager
from datetime import datetime

from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.db import connection

from .locations_recalculate_areas_and_lengths import Command as RecalculateAreasAndLengthCommand
from .locations_recalculate_slugs import Command as RecalculateSlugs
from .locations_recalculate_denormalized_fields import Command as RecalculateDenormalizedFields


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
        start = datetime.now()
        self.stdout.write('{} - {}...'.format(start.strftime('%H:%M:%S'), label))
        try:
            yield
        finally:
            took = datetime.now() - start
            self.timings.append((label, took))
            self.stdout.write('  {} took {}'.format(label, took))

    def add_arguments(self, parser):
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
        started = datetime.now()
        with self.timed('areas and lengths'):
            call_command('locations_recalculate_areas_and_lengths')

        with self.timed('cleaning denorm fields'):
            RecalculateDenormalizedFields.clean_denorm_fields_before_update()
        # Countries have nothing to be qualified by, so their slugs are
        # already final and the walk can start by pushing them down.
        with self.timed('slugs: Country'):
            call_command('locations_recalculate_slugs', 'Country')
        for ancestor, descendant in self.LEVELS:
            with self.timed('ancestry: {} down'.format(ancestor)):
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
                with self.timed('streets without a place'):
                    RecalculateDenormalizedFields.update_street_place_field()
            with self.timed('slugs: {}'.format(descendant)):
                call_command('locations_recalculate_slugs', descendant)
        # After the walk, because it copies each place's finished ancestry and
        # final slug onto the addresses inside it, and before the placeholder
        # is stripped, so the copy lands in the same shape the containment pass
        # produced.
        with self.timed('addresses inherit from their place'):
            RecalculateDenormalizedFields.inherit_address_ancestry_from_place()

        # After the streets are slugged, because an address stores its
        # street's slug, and by name rather than by containment -- an address
        # says which street it is on, and a point is not inside a line.
        with self.timed('addresses to streets'):
            RecalculateDenormalizedFields.update_address_street_field()
        with self.timed('stripping placeholders'):
            RecalculateDenormalizedFields.clean_denorm_fields_after_update()
        # After the strip: an address's slug reads its parent from element [1],
        # which until now was the placeholder.
        with self.timed('address slugs'):
            RecalculateDenormalizedFields.update_address_slugs()

        # The tsvector columns are not rebuilt here. Search moved to
        # OpenSearch, where an edge n-gram analyser does the prefix matching
        # they used to -- search/indexes.py says as much -- and nothing in the
        # package queries them any more. Building them cost about two hours of
        # every recalculation, most of it one UPDATE over 2.8 million streets.
        # The columns are left in place for anything downstream still reading
        # them; run locations_recalculate_denormalized_fields to refresh them.

        self.report_write_amplification()

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

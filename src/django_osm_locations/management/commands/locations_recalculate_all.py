from django.core.management.base import BaseCommand
from django.core.management import call_command

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
        call_command('locations_recalculate_areas_and_lengths')

        RecalculateDenormalizedFields.clean_denorm_fields_before_update()
        # Countries have nothing to be qualified by, so their slugs are
        # already final and the walk can start by pushing them down.
        call_command('locations_recalculate_slugs', 'Country')
        for ancestor, descendant in self.LEVELS:
            call_command(
                'locations_recalculate_denormalized_fields', ancestor,
                no_clean_before=True, no_clean_after=True, no_search_vector=True,
            )
            call_command('locations_recalculate_slugs', descendant)
        RecalculateDenormalizedFields.clean_denorm_fields_after_update()

        # The tsvector columns are not rebuilt here. Search moved to
        # OpenSearch, where an edge n-gram analyser does the prefix matching
        # they used to -- search/indexes.py says as much -- and nothing in the
        # package queries them any more. Building them cost about two hours of
        # every recalculation, most of it one UPDATE over 2.8 million streets.
        # The columns are left in place for anything downstream still reading
        # them; run locations_recalculate_denormalized_fields to refresh them.

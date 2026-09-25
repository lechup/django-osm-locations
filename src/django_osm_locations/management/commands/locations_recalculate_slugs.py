from collections import defaultdict

from django.contrib.gis.db.models.functions import Distance
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Count, F, Func, Q
from ...utils.progress import progress
from ...utils.query_log import stop_logging_queries

from .. import Country, CountrySubarea, Place, PlaceSubarea, Street
from ...utils.slugify import slugify


class Command(BaseCommand):
    """
    Give every location a unique, readable slug.

    Slugs are public: places appear as subdomains, and everything appears in
    URLs. Duplicates used to be resolved by appending the OSM id, which gave
    ``station-road-25986049`` -- unique, but not something anyone would type or
    recognise.

    A duplicate is now qualified by *where it is*, using names that are already
    on the row:

        station-road-ramsey        street, qualified by its place
        church-street-peel
        wieliczka-krakow           place, qualified by the nearest larger place

    The qualifier is appended with a plain hyphen and no connector word --
    "near", "bei", "koło" would each be right in only one language, and the
    qualifier is itself a local name, so the result reads naturally wherever it
    is used.

    The biggest of a set keeps the bare slug, so ``douglas`` stays ``douglas``
    and only its namesakes are qualified. The OSM id remains as a last resort
    for the rare cases geography cannot separate -- two streets of the same name
    in the same town.
    """

    help = (
        "Recalculate slugs. Duplicates are qualified by their parent location, "
        "or for places by the nearest larger place, falling back to the OSM id "
        "only when that cannot separate them."
    )

    # How a duplicate of each type is qualified, in order of preference.
    # 'nearest' means the nearest larger place; the rest are denormalised
    # ancestry fields already present on the row.
    QUALIFIERS = {
        Country: (),
        CountrySubarea: ('country',),
        Place: ('nearest', 'countrysubarea', 'country'),
        PlaceSubarea: ('place', 'countrysubarea'),
        Street: ('place', 'placesubarea', 'countrysubarea'),
    }

    # Radius for the nearest-larger-place search, in degrees. Roughly 110km at
    # this latitude -- wide enough to reach a city from a village, narrow enough
    # that the query stays bounded.
    NEAREST_RADIUS = 1.0

    #: Slugs are written per batch rather than per row.
    BATCH_SIZE = 1000

    #: Columns the qualifier cascade never reads, deferred so that resolving a
    #: slug does not drag a street's geometry or a tsvector across the wire.
    MAYBE_HEAVY = ('border', 'lines', 'search_vector', 'search_vector_name')

    #: The country a row belongs to, whether or not the placeholder is still
    #: there. clean_denorm_fields_before_update puts ['',''] at position 1 and
    #: array_cat appends after it, so mid-walk the real ancestor sits at 2 and
    #: after the strip at 1. Both, in one expression.
    SCOPE_SQL = "coalesce(nullif(country[1][2], ''), country[2][2])"

    def add_arguments(self, parser):
        parser.add_argument(
            'types', nargs='*',
            help='Model names to recalculate. Defaults to all of them.',
        )

    @classmethod
    def set_default_slugs(cls, model_class):
        """Base slug from the name, via the database's own slugify()."""
        model_class.objects.annotate(slugified=Func(F('name'), function='slugify')).update(
            slug=F('slugified')
        )

    @classmethod
    def _rank(cls, model_class):
        """
        Order that decides which duplicate keeps the bare slug.

        Rows flagged as merged come last whatever their size. They are
        duplicates the import already resolved and they are not in the search
        index, so letting one hold ``coast-road-german`` while the live street
        is pushed onto an OSM id gets it exactly backwards.

        Then population where the model has it -- the larger town is the one
        people mean -- then the model's own ordering, which is size. Population
        is sparse in OSM (6 of 90 places on the Isle of Man carry it), so the
        size fallback does most of the work.
        """
        field_names = {f.name for f in model_class._meta.get_fields()}
        fields = []
        if 'is_merged' in field_names:
            fields.append(F('is_merged').asc())
        if 'population' in field_names:
            fields.append(F('population').desc(nulls_last=True))
        for ordering in model_class._meta.ordering or []:
            fields.append(ordering)
        fields.append('id')
        return fields

    @classmethod
    def _ancestry_qualifier(cls, obj, field):
        """The slug of a denormalised ancestor, e.g. the place a street is in."""
        for pair in getattr(obj, field, None) or []:
            if len(pair) > 1 and pair[1]:
                return pair[1]
        return None

    @classmethod
    def _nearest_qualifier(cls, obj, model_class):
        """
        The nearest place larger than this one.

        "Larger" is by population where both have it, otherwise by area, which
        is the same ranking that decides who keeps the bare slug.
        """
        if obj.center is None:
            return None

        bigger = Q(population__gt=obj.population or 0) | Q(area__gt=obj.area or 0)
        candidates = (
            Place.objects
            .exclude(pk=obj.pk)
            .exclude(center=None)
            .filter(bigger)
            .filter(center__dwithin=(obj.center, cls.NEAREST_RADIUS))
            .annotate(distance=Distance('center', obj.center))
            .order_by('distance')
            .only('slug')
        )
        nearest = candidates.first()
        return nearest.slug if nearest else None

    @classmethod
    def _qualifiers_for(cls, obj, model_class):
        """Candidate qualifiers for one object, best first."""
        for field in cls.QUALIFIERS.get(model_class, ()):
            if field == 'nearest':
                value = cls._nearest_qualifier(obj, model_class)
            else:
                value = cls._ancestry_qualifier(obj, field)
            if value:
                yield value

    @classmethod
    def scopes_for(cls, model_class):
        """
        (slug, country) for every row, so slugs can be made unique per country.

        A slug is unique within a country, not across the world. That is what
        lets one country's index be built, published and merged with another's
        without either one's public identifiers moving: adding Denmark cannot
        rename a Polish street.

        One pass. The denormalised arrays carry no GIN index, and `country` is
        a two-dimensional array rather than nested ones, so a filter per
        country would be a sequential scan per country -- ten of them over 2.8
        million streets.
        """
        table = model_class._meta.db_table
        if 'country' not in (getattr(model_class, 'denorm_fields', None) or ()):
            # Country itself: its slugs are the country names, and those are
            # the one thing that must stay globally unique.
            with connection.cursor() as cursor:
                cursor.execute('SELECT slug, %s FROM "{}"'.format(table), [''])
                yield from cursor
            return
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT slug, {} FROM "{}"'.format(cls.SCOPE_SQL, table)
            )
            while True:
                rows = cursor.fetchmany(10000)
                if not rows:
                    return
                yield from rows

    @classmethod
    def duplicates_for(cls, model_class):
        """
        (country, slug, ids) for every base slug held by more than one row in
        the same country. Grouped in SQL so only the collisions come back.
        """
        table = model_class._meta.db_table
        scope = "''" if 'country' not in (
            getattr(model_class, 'denorm_fields', None) or ()) else cls.SCOPE_SQL
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT {scope} AS scope, slug, array_agg(id) AS ids
                FROM "{table}"
                GROUP BY {scope}, slug
                HAVING count(*) > 1
            """.format(scope=scope, table=table))
            return cursor.fetchall()

    @classmethod
    def set_unique_slugs(cls, model_class):
        """
        Resolve duplicate slugs by qualifying all but the largest of each set,
        within each country.
        """
        taken = defaultdict(set)
        for slug, scope in cls.scopes_for(model_class):
            taken[scope].add(slug)

        duplicated = cls.duplicates_for(model_class)
        if not duplicated:
            return

        pending = []
        for scope, base_slug, ids in progress(
                duplicated, desc=model_class.__name__, leave=False):
            objects = list(
                model_class.objects.filter(pk__in=ids)
                .defer(*cls._heavy_fields(model_class))
                .order_by(*cls._rank(model_class))
            )
            # the largest keeps the bare slug
            for obj in objects[1:]:
                obj.slug = cls._unique_slug_for(
                    obj, model_class, base_slug, taken[scope])
                taken[scope].add(obj.slug)
                pending.append(obj)
                if len(pending) >= cls.BATCH_SIZE:
                    model_class.objects.bulk_update(pending, ['slug'])
                    pending = []
        if pending:
            model_class.objects.bulk_update(pending, ['slug'])

    @classmethod
    def _heavy_fields(cls, model_class):
        """
        Geometry and tsvector columns to leave behind when loading duplicates.

        Uniquification reads names, ranks and denormalised ancestry; it never
        looks at a border or a line string. Loading them turned every duplicate
        into a GEOS deserialisation, and 92% of street rows are duplicates.
        ``center`` stays: Place qualifies by the nearest larger place.
        """
        present = {f.name for f in model_class._meta.get_fields()}
        return tuple(name for name in cls.MAYBE_HEAVY if name in present)

    @classmethod
    def _unique_slug_for(cls, obj, model_class, base_slug, taken):
        """
        Walk the qualifier cascade until something unused turns up.

        The OSM id is the backstop: geography cannot separate two streets of the
        same name in the same town, and something has to give.
        """
        for qualifier in cls._qualifiers_for(obj, model_class):
            if qualifier == base_slug:
                # 'douglas' in Douglas adds nothing
                continue
            candidate = '{}-{}'.format(base_slug, slugify(qualifier))
            if candidate not in taken:
                return candidate

        osm_id = obj.relation_osm_id or obj.way_osm_id or obj.node_osm_id or obj.id
        return '{}-{}'.format(base_slug, osm_id)

    def handle(self, *args, **options):
        stop_logging_queries(self.stdout)
        # Address is deliberately absent. Its slug is derived from its street
        # and house number, which is unique by construction, and putting 8.5
        # million of them through duplicate resolution would be enormous and
        # pointless.
        models = [Country, CountrySubarea, Place, PlaceSubarea, Street]
        selected = options.get('types')
        if selected:
            wanted = {name.lower() for name in selected}
            models = [m for m in models if m.__name__.lower() in wanted]

        for model_class in models:
            self.stdout.write('{} - calculating default slugs...'.format(model_class.__name__))
            with transaction.atomic():
                self.set_default_slugs(model_class)
            self.stdout.write('{} - resolving duplicates...'.format(model_class.__name__))
            with transaction.atomic():
                self.set_unique_slugs(model_class)

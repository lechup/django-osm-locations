from django.contrib.gis.db.models.functions import Distance
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, F, Func, Q
from tqdm import tqdm

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
    def set_unique_slugs(cls, model_class):
        """
        Resolve duplicate slugs by qualifying all but the largest of each set.
        """
        taken = set(
            model_class.objects.values_list('slug', flat=True)
        )
        duplicated = (
            model_class.objects.values('slug')
            .annotate(n=Count('slug'))
            .filter(n__gt=1)
            .values_list('slug', flat=True)
            .order_by()
        )
        duplicated = list(duplicated)
        if not duplicated:
            return

        for base_slug in tqdm(duplicated, desc=model_class.__name__, leave=False):
            objects = list(
                model_class.objects.filter(slug=base_slug).order_by(*cls._rank(model_class))
            )
            # the largest keeps the bare slug
            for obj in objects[1:]:
                obj.slug = cls._unique_slug_for(obj, model_class, base_slug, taken)
                obj.save(update_fields=['slug'])
                taken.add(obj.slug)

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

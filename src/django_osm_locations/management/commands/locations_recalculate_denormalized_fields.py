from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, connection
from django.utils.text import slugify
from ...utils.progress import progress

from .. import Country, CountrySubarea, Place, PlaceSubarea, Street


class Command(BaseCommand):
    help = "Recalculate all denormalized fields for locations objects like CountrySubarea.country."

    DENORMALIZED_MODEL_CLASSES = [Country, CountrySubarea, Place, PlaceSubarea, Street]
    MODEL_CLASSES_TO_UPDATE = DENORMALIZED_MODEL_CLASSES[:4]
    MODEL_CLASSES_TO_CLEAN = DENORMALIZED_MODEL_CLASSES[1:]
    MODEL_CASSES_TO_SEARCH_UPDATE = DENORMALIZED_MODEL_CLASSES
    DENORMALIZED_MODEL_CLASSES_TO_UPDATE_DICT = {
        'Country': DENORMALIZED_MODEL_CLASSES[1:],
        'CountrySubarea': DENORMALIZED_MODEL_CLASSES[1:],
        'Place': DENORMALIZED_MODEL_CLASSES[2:],
        'PlaceSubarea': DENORMALIZED_MODEL_CLASSES[3:],
    }

    def add_arguments(self, parser):
        parser.add_argument(
            'types', nargs='*',
            help='Ancestor models to push down. Defaults to all of them.',
        )
        parser.add_argument(
            '-a',
            '--all',
            action='store_true',
            default=False,
            help='Calculate all fields even those that currently have value. By default we select only those locations'
                 'that have all denorm fields unset (len = 0).'
        )
        parser.add_argument(
            '--no-clean-before', action='store_true', default=False,
            help='Do not blank the denormalised arrays first. For a caller that '
                 'cleans once around several partial runs.',
        )
        parser.add_argument(
            '--no-clean-after', action='store_true', default=False,
            help='Leave the placeholder element in place for a later pass to strip.',
        )
        parser.add_argument(
            '--no-search-vector', action='store_true', default=False,
            help='Skip rebuilding the search vectors, which are only correct '
                 'once every ancestor has been pushed down.',
        )

    @classmethod
    def selected_model_classes(cls, names):
        """
        The ancestor models to push down, by name, defaulting to all of them.

        Only the four that have descendants can be selected: nothing sits below
        a Street, so it is written to but never written from.
        """
        if not names:
            return list(cls.MODEL_CLASSES_TO_UPDATE)
        wanted = {name.lower() for name in names}
        chosen = [m for m in cls.MODEL_CLASSES_TO_UPDATE if m.__name__.lower() in wanted]
        unknown = wanted - {m.__name__.lower() for m in cls.MODEL_CLASSES_TO_UPDATE}
        if unknown:
            raise CommandError('not an ancestor model: {}. Choose from {}.'.format(
                ', '.join(sorted(unknown)),
                ', '.join(m.__name__ for m in cls.MODEL_CLASSES_TO_UPDATE),
            ))
        return chosen

    @classmethod
    def get_sql_for_update_denorm_fields(cls, model_name, field_name, ancestor_table):
        """
        Build the denormalisation UPDATE.

        Only table and column names are interpolated -- all from model class
        names, never from data. The name, slug and the ancestor's id travel as
        bound parameters.

        The border is read from the database rather than sent to it. It used
        to travel as ``obj.border.hexewkb``: the geometry was loaded into
        Python, hex-encoded to roughly twice its size, and shipped back for
        the server to parse again -- once per descendant table. That was
        tolerable while borders were small, and stopped being tolerable the
        moment they were clipped to the coastline, which took the United
        Kingdom's border from 67,000 vertices to 2.2 million: a single
        statement carrying a 72MB string, four times over, with the whole
        hierarchy amounting to about a gigabyte of transient strings per pass.
        The user session was killed by systemd-oomd under the resulting memory
        pressure. A subquery on the primary key costs three small parameters
        and resolves to an InitPlan, evaluated once.
        """
        return """
            UPDATE "locations_{model_name}" SET {field_name} =
            array_cat({field_name}, ARRAY[%s, %s]::varchar[]) WHERE
              ST_Intersects(center, (SELECT border FROM "{ancestor_table}" WHERE id = %s))
            ;
        """.format(**{
            'model_name': model_name,
            'field_name': field_name,
            'ancestor_table': ancestor_table,
        })

    @classmethod
    def get_sql_for_clean_denorm_fields(cls, model_name, field_names):
        """
        Strip the placeholder from every denormalised column in one pass.

        This used to be one UPDATE per column. A street has four of them, so
        cleaning up after a recalculation rewrote all 2.8 million rows four
        times over -- about twenty minutes each, and the slowest phase of the
        whole pipeline by a distance. The work per column is trivial; it was
        the repeated full-table rewrite that cost.
        """
        placeholder = "ARRAY[ARRAY['','']]::varchar[]"
        assignments = ',\n                '.join(
            "{field} = CASE WHEN {field} @> {placeholder} "
            "THEN {field}[2:array_upper({field}, 1)] ELSE {field} END".format(
                field=field, placeholder=placeholder,
            )
            for field in field_names
        )
        condition = '\n               OR '.join(
            '{field} @> {placeholder}'.format(field=field, placeholder=placeholder)
            for field in field_names
        )
        return """
            UPDATE "locations_{model_name}" SET
                {assignments}
            WHERE {condition};
        """.format(model_name=model_name, assignments=assignments, condition=condition)

    @classmethod
    @transaction.atomic
    def clean_denorm_fields_after_update(cls):
        """
        Remove dummy empty element {'', ''}, we are adding during cls.clean_denorm_fields_before_update() from all
        denorm fields.
        """
        for model_class_to_clean in cls.MODEL_CLASSES_TO_CLEAN:
            print("{} - cleaning denorm fields after update...".format(model_class_to_clean.__name__))
            sql = cls.get_sql_for_clean_denorm_fields(
                model_class_to_clean.__name__.lower(),
                model_class_to_clean.denorm_fields,
            )
            with connection.cursor() as cursor:
                cursor.execute(sql)

    @classmethod
    def update_denorm_fields_for_model_class(cls, model_class_to_update):
        """
        Recaltulate all denorm fields for :param model_class_to_update:. We iterate throught other location models
        defined in `cls.DENORMALIZED_MODEL_CLASSES_TO_UPDATE_DICT` and update proper fields for
        :param model_class_to_update:. So for `Country` model we set `country` field on `[CountrySubarea, Place,
        PlaceSubarea, Street]` models and so on.

        :param model_class_to_update: model class
        """
        # border deliberately not deferred-in: it is read server-side now, and
        # loading it here pulled half a gigabyte of geometry through Python
        objects = model_class_to_update.objects.exclude(border=None).only('name', 'slug').iterator()
        objects_count = model_class_to_update.objects.exclude(border=None).count()
        for obj in progress(objects, total=objects_count, desc=model_class_to_update.__name__):
            model_name = model_class_to_update.__name__
            denormalized_model_classes = progress(
                cls.DENORMALIZED_MODEL_CLASSES_TO_UPDATE_DICT[model_name],
                leave=False,
                desc=slugify(
                    'Updating {}'.format(
                        obj.name
                    )
                )
            )
            for denormalized_model_class in denormalized_model_classes:
                field_name = model_class_to_update.__name__.lower()
                model_name = denormalized_model_class.__name__.lower()
                sql = cls.get_sql_for_update_denorm_fields(
                    model_name, field_name, model_class_to_update._meta.db_table,
                )
                with connection.cursor() as cursor:
                    cursor.execute(sql, [obj.name, obj.slug, obj.id])

    @classmethod
    @transaction.atomic
    def clean_denorm_fields_before_update(cls):
        """
        Set dummy empty element {'', ''}, as a default to all denorm fields. We need it during
        cls.update_denorm_fields() because postgres needs element to concat multidimensional arrays.
        """
        for model_class_to_clean in cls.MODEL_CLASSES_TO_CLEAN:
            print("{} - cleaning denorm fields before update...".format(model_class_to_clean.__name__))
            update_dict = {}
            for field_name in model_class_to_clean.denorm_fields:
                update_dict[field_name] = [['', '']]

            if hasattr(model_class_to_clean, 'objects_all'):
                model_class_to_clear_all = model_class_to_clean.objects_all
            else:
                model_class_to_clear_all = model_class_to_clean.objects

            model_class_to_clear_all.all().update(
                **update_dict
            )

    @classmethod
    def update_denorm_fields(cls):
        """
        Recalculate all denormalized fields for locations models.
        """
        for model_class_to_update in cls.MODEL_CLASSES_TO_UPDATE:
            print("{} - updating denorm fields...".format(model_class_to_update.__name__))
            cls.update_denorm_fields_for_model_class(model_class_to_update)

    #: How far a street will reach for a place to belong to. Matches the
    #: radius Place.near_bbox already uses. Uncapped, a road in open country
    #: is attributed to whatever town is nearest even at 40km, which says
    #: less than saying nothing.
    NEAREST_PLACE_KM = 25

    @classmethod
    def get_sql_for_street_place_field(cls):
        """
        Give a street with no place the nearest one.

        A street only gets a place from the point-in-polygon pass if it falls
        inside a place's border, and 771,253 of Poland's and the UK's live
        streets fall inside none -- rural roads, trunk roads between towns.
        They keep their country and country subarea, so they are findable,
        but they have no town in their ancestry and nothing to qualify a
        duplicate slug against.

        This used to be a Python loop doing a nearest-place query and a
        ``save()`` per street, which measured about three rows a second: some
        seventy hours for the streets that need it, which is presumably why
        it was written and never called. One statement with a lateral join
        does the same work, and ``<->`` on the GiST index over Place.center
        makes each lookup an index walk rather than a scan.
        """
        return """
            UPDATE "locations_street" AS s SET place = x.pair
            FROM (
                SELECT s2.id, ARRAY[ARRAY[p.name, p.slug]]::varchar[] AS pair
                FROM "locations_street" s2
                CROSS JOIN LATERAL (
                    SELECT name, slug, center
                    FROM "locations_place"
                    WHERE center IS NOT NULL AND NOT is_merged
                    ORDER BY center <-> s2.center
                    LIMIT 1
                ) p
                WHERE s2.center IS NOT NULL
                  AND (s2.place IS NULL
                       OR array_length(s2.place, 1) IS NULL
                       -- mid-walk the arrays still carry the placeholder, and
                       -- an array holding only ['',''] is an empty one. This
                       -- has to run before streets are slugged, or the 771,321
                       -- strays are qualified with no place to qualify by.
                       OR s2.place = ARRAY[ARRAY['','']]::varchar[])
                  AND ST_DWithin(p.center::geography, s2.center::geography, %s)
            ) AS x
            WHERE s.id = x.id;
        """

    @classmethod
    @transaction.atomic
    def update_street_place_field(cls):
        print("Street - attaching streets that fall inside no place...")
        with connection.cursor() as cursor:
            cursor.execute(
                cls.get_sql_for_street_place_field(),
                [cls.NEAREST_PLACE_KM * 1000],
            )
            print("  {} attached".format(cursor.rowcount))

    @classmethod
    def get_sql_for_search_vector(cls, model_class):
        search_concat_args = []
        if hasattr(model_class, 'denorm_fields'):
            for denorm_field in model_class.denorm_fields:
                search_concat_args.append(
                    """replace(
                        slugify(
                            array_to_string(
                                {denorm_field}[1:array_length({denorm_field}, 1)][1],
                                ' '
                            )
                        ),
                        '-',
                        ' '
                    )""".format(**{
                        'denorm_field': denorm_field
                    })
                )
                search_concat_args.append("' '")
        search_concat_args.append("replace(slugify(name), '-', ' ')")
        return """
            UPDATE "locations_{model_name}" SET search_vector = to_tsvector('english', concat({search_concat_args}));
            UPDATE "locations_{model_name}" SET search_vector_name = to_tsvector('english', slugify(name));
        """.format(**{
            'model_name': model_class.__name__.lower(),
            'search_concat_args': ", ".join(search_concat_args)
        })

    @classmethod
    @transaction.atomic
    def update_search_vector_field(cls):
        for denormalized_model_class in cls.DENORMALIZED_MODEL_CLASSES:
            print("{} - updating search vector field...".format(denormalized_model_class.__name__))
            sql = cls.get_sql_for_search_vector(denormalized_model_class)
            with connection.cursor() as cursor:
                cursor.execute(sql)

    def handle(self, *args, **options):
        model_classes = self.selected_model_classes(options.get('types'))

        if not options.get('no_clean_before'):
            self.clean_denorm_fields_before_update()
        for model_class in model_classes:
            print("{} - updating denorm fields...".format(model_class.__name__))
            self.update_denorm_fields_for_model_class(model_class)
        if not options.get('no_clean_after'):
            self.clean_denorm_fields_after_update()
            # only meaningful once every place has been pushed down, and the
            # placeholder element stripped
            self.update_street_place_field()
        if not options.get('no_search_vector'):
            self.update_search_vector_field()

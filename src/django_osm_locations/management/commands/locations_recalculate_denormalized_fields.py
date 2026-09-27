from datetime import datetime

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, connection
from django.utils.text import slugify
from ...utils.progress import progress
from ...utils.query_log import stop_logging_queries

from .. import Address, Country, CountrySubarea, Place, PlaceSubarea, Street


class Command(BaseCommand):
    help = "Recalculate all denormalized fields for locations objects like CountrySubarea.country."

    #: The hierarchy, coarsest first. Address is last because nothing sits
    #: below a house number, and it is the level that lets a full parent path
    #: be printed: Polska > pomorskie > Gdansk > Lakowa > Lakowa 5.
    DENORMALIZED_MODEL_CLASSES = [
        Country, CountrySubarea, Place, PlaceSubarea, Street, Address,
    ]
    #: Sources for the point-in-polygon push-down, which needs a border.
    #: Street has none -- it has lines -- so an address gets its street by
    #: name (update_address_street_field) rather than by containment.
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
            '--one-pass', action='store_true', default=False,
            help='Push a whole ancestor level down in one statement instead of '
                 'one per ancestor row. Fewer row rewrites -- a street inside a '
                 'voivodeship, a county and a commune is rewritten once rather '
                 'than three times -- but it sorts every match and hash-joins '
                 'the whole table, which the planner costs as 21 million rows '
                 'for addresses. Off by default because it has not been shown '
                 'to be faster; turn it on to measure.',
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
    def get_sql_for_update_denorm_fields_in_one_pass(cls, model_name, field_name,
                                                     ancestor_table):
        """
        Push one whole ancestor level down in a single statement.

        The per-ancestor version issues one UPDATE for every ancestor row:
        3,040 country subareas across five descendant tables is 15,200
        statements, and a descendant contained by a voivodeship, a county and
        a commune -- all three being CountrySubarea -- is rewritten three
        times. Every rewrite is a new row version, and because the table is
        packed at fillfactor 100 with seventeen indexes on it, none of which
        covers the denormalised arrays, each one also maintains all seventeen.

        Aggregating first rewrites each row once per level instead of once per
        matching ancestor. The order is what the slug pass depends on: it reads
        the first pair, and the ancestors were iterated largest-first, so the
        aggregate is ordered by area descending to keep that.

        ``array_agg`` of a two-element array builds the same N x 2 shape the
        column already holds, so ``array_cat`` appends it as further rows.
        """
        return """
            UPDATE "locations_{model_name}" AS d
            SET {field_name} = array_cat(d.{field_name}, a.pairs)
            FROM (
                SELECT d2.id,
                       array_agg(ARRAY[anc.name, anc.slug]::varchar[]
                                 ORDER BY anc.area DESC NULLS LAST, anc.id) AS pairs
                FROM "locations_{model_name}" d2
                JOIN "{ancestor_table}" anc
                  ON anc.border IS NOT NULL
                 AND ST_Intersects(d2.center, anc.border)
                GROUP BY d2.id
            ) AS a
            WHERE d.id = a.id;
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
    def update_denorm_fields_for_model_class(cls, model_class_to_update, one_pass=False,
                                             report=None):
        """
        Recaltulate all denorm fields for :param model_class_to_update:. We iterate throught other location models
        defined in `cls.DENORMALIZED_MODEL_CLASSES_TO_UPDATE_DICT` and update proper fields for
        :param model_class_to_update:. So for `Country` model we set `country` field on `[CountrySubarea, Place,
        PlaceSubarea, Street]` models and so on.

        :param model_class_to_update: model class
        """
        # border deliberately not deferred-in: it is read server-side now, and
        # loading it here pulled half a gigabyte of geometry through Python
        if one_pass:
            return cls._update_denorm_fields_in_one_pass(model_class_to_update, report)

        objects = model_class_to_update.objects.exclude(border=None).only('name', 'slug').iterator()
        objects_count = model_class_to_update.objects.exclude(border=None).count()
        if report:
            report('{} - pushing down from {} ancestors, one statement each'.format(
                model_class_to_update.__name__, objects_count))
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
                    cls._tally(model_class_to_update, denormalized_model_class,
                               cursor.rowcount)

    #: rows rewritten per (ancestor model, descendant model), so a pass can say
    #: where its writes went rather than only how long it took. A descendant
    #: rewritten once per matching ancestor shows up here as a multiple of its
    #: row count, which is the thing worth noticing.
    written = None

    @classmethod
    def _tally(cls, ancestor_model, descendant_model, rows):
        if cls.written is None:
            cls.written = {}
        key = (ancestor_model.__name__, descendant_model.__name__)
        got = cls.written.setdefault(key, [0, 0])
        got[0] += 1
        got[1] += max(rows, 0)

    @classmethod
    def _update_denorm_fields_in_one_pass(cls, model_class_to_update, report=None):
        """One statement per descendant table; see the SQL builder for why."""
        for descendant in cls.DENORMALIZED_MODEL_CLASSES_TO_UPDATE_DICT[
                model_class_to_update.__name__]:
            field_name = model_class_to_update.__name__.lower()
            model_name = descendant.__name__.lower()
            sql = cls.get_sql_for_update_denorm_fields_in_one_pass(
                model_name, field_name, model_class_to_update._meta.db_table,
            )
            started = datetime.now()
            with connection.cursor() as cursor:
                cursor.execute(sql)
                rows = cursor.rowcount
                cls._tally(model_class_to_update, descendant, rows)
            if report:
                report('  {} -> {}: {} rows in {}'.format(
                    model_class_to_update.__name__, descendant.__name__,
                    rows, datetime.now() - started))

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

    #: How near an address has to be to count as being on a street.
    #: Generous enough for a building set back from the road, tight enough
    #: that the next street over does not vote.
    ADDRESS_MATCH_METRES = 100

    @classmethod
    def get_sql_for_address_street_link(cls):
        """
        Point each address at the street it names, and give it that ancestry.

        The match is by name within a settlement, because that is what an
        address states: addr:street says which street, addr:city or addr:place
        says which settlement. Geometry only breaks ties -- the nearest street
        of that name, within ADDRESS_MATCH_METRES.
        """
        return """
            UPDATE "locations_address" AS a SET street = x.pair
            FROM (
                SELECT a2.id, ARRAY[ARRAY[s.name, s.slug]]::varchar[] AS pair
                FROM "locations_address" a2
                CROSS JOIN LATERAL (
                    SELECT name, slug
                    FROM "locations_street"
                    WHERE NOT is_merged
                      AND name = a2.street_name
                      AND center IS NOT NULL
                      AND ST_DWithin(center::geography, a2.center::geography, %s)
                    ORDER BY center <-> a2.center
                    LIMIT 1
                ) s
                WHERE a2.street_name <> '' AND a2.center IS NOT NULL
            ) AS x
            WHERE a.id = x.id;
        """

    @classmethod
    def get_sql_for_street_settlement_from_addresses(cls):
        """
        Give a street the settlement its own addresses name.

        This is the one place here that reads rather than infers. 64% of
        Poland's addressed features and 80% of Britain's name both a street
        and a settlement, against 0.6% of roads carrying addr:city themselves,
        so for most streets the answer is written down -- and a street whose
        addresses say Gdansk is in Gdansk, whatever happens to be nearest.

        The modal settlement wins, because a long road can carry addresses
        from more than one village and the majority is the better answer.
        """
        return """
            UPDATE "locations_street" AS s SET place = x.pair
            FROM (
                SELECT ranked.street_id, ARRAY[ARRAY[p.name, p.slug]]::varchar[] AS pair
                FROM (
                    SELECT DISTINCT ON (v.street_id)
                           v.street_id, v.settlement_name
                    FROM (
                        SELECT st.id AS street_id, a.settlement_name, count(*) AS votes
                        FROM "locations_street" st
                        JOIN "locations_address" a
                          ON a.street_name = st.name
                         AND a.settlement_name <> ''
                         AND ST_DWithin(a.center::geography, st.center::geography, %s)
                        WHERE NOT st.is_merged AND st.center IS NOT NULL
                        GROUP BY st.id, a.settlement_name
                    ) v
                    ORDER BY v.street_id, v.votes DESC, v.settlement_name
                ) ranked
                JOIN "locations_street" st2 ON st2.id = ranked.street_id
                -- A name alone does not pick a place: nearly half of Polish
                -- places share theirs with another, and a plain join on the
                -- name matched every Kowale there is. The nearest namesake
                -- wins. Keep this comment free of the percent character:
                -- psycopg2 scans the whole string for placeholders, comments
                -- included, and a stray one shifts the parameter count.
                CROSS JOIN LATERAL (
                    SELECT name, slug
                    FROM "locations_place" p
                    WHERE p.name = ranked.settlement_name
                      AND NOT p.is_merged AND p.center IS NOT NULL
                    ORDER BY p.center <-> st2.center
                    LIMIT 1
                ) p
            ) AS x
            WHERE s.id = x.street_id;
        """

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
    def get_sql_for_address_slugs(cls):
        """
        Derive each address's slug from what it now knows it is on.

        _presave_slug writes a slug only when there is none, which is at
        import -- before the street array exists -- so every address came out
        as "lakowa-5": 8.5 million rows, and nothing later touched them,
        because Address is deliberately kept out of the disambiguation pass.
        Derived is still derived; it just has to be derived after the street
        is linked. Mirrors Address.slugify exactly, so the two never drift.

        Runs after the placeholder is stripped: until then element [1] is
        ['',''] and the real parent sits at [2].
        """
        return """
            UPDATE "locations_address" SET slug = CASE
                WHEN street[1][2] IS NOT NULL AND street[1][2] <> ''
                    THEN slugify(street[1][2] || '-' || house_number)
                WHEN place[1][2] IS NOT NULL AND place[1][2] <> ''
                    THEN slugify(place[1][2] || '-' || house_number)
                ELSE slugify(name)
            END;
        """

    @classmethod
    @transaction.atomic
    def update_address_slugs(cls):
        print("Address - deriving slugs from the linked street...")
        with connection.cursor() as cursor:
            cursor.execute(cls.get_sql_for_address_slugs())
            print("  {} derived".format(cursor.rowcount))

    @classmethod
    @transaction.atomic
    def update_address_street_field(cls):
        print("Address - linking addresses to the street they name...")
        with connection.cursor() as cursor:
            cursor.execute(
                cls.get_sql_for_address_street_link(), [cls.ADDRESS_MATCH_METRES],
            )
            print("  {} linked".format(cursor.rowcount))

    @classmethod
    @transaction.atomic
    def update_street_place_field(cls):
        """
        Give every street a settlement, best evidence first.

        An address states which settlement a street is in; a nearest-place
        search guesses. So the addresses go first and the guess only fills
        what they leave.
        """
        with connection.cursor() as cursor:
            print("Street - reading the settlement from its own addresses...")
            cursor.execute(
                cls.get_sql_for_street_settlement_from_addresses(),
                [cls.ADDRESS_MATCH_METRES],
            )
            print("  {} from addresses".format(cursor.rowcount))

            print("Street - attaching what is left to the nearest place...")
            cursor.execute(
                cls.get_sql_for_street_place_field(),
                [cls.NEAREST_PLACE_KM * 1000],
            )
            print("  {} by proximity".format(cursor.rowcount))

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
        stop_logging_queries(self.stdout)
        model_classes = self.selected_model_classes(options.get('types'))

        if not options.get('no_clean_before'):
            self.clean_denorm_fields_before_update()
        self.written = {}
        for model_class in model_classes:
            started = datetime.now()
            print("{} - updating denorm fields...".format(model_class.__name__))
            self.update_denorm_fields_for_model_class(
                model_class, one_pass=options.get('one_pass'),
                report=self.stdout.write,
            )
            self.stdout.write('  {} pushed down in {}'.format(
                model_class.__name__, datetime.now() - started))
        self.report_writes()
        if not options.get('no_clean_after'):
            self.clean_denorm_fields_after_update()
            # only meaningful once every place has been pushed down, and the
            # placeholder element stripped
            self.update_street_place_field()
        if not options.get('no_search_vector'):
            self.update_search_vector_field()

    def report_writes(self):
        """
        How many row rewrites each level cost, and how many rows that is per
        row of the table.

        Where the time goes is not the statements, it is the writes: every
        rewrite is a new row version, and with the table packed at fillfactor
        100 and no index covering the denormalised arrays, each one maintains
        every index on the table. A ratio well above 1 is the signal that a
        level is rewriting the same rows repeatedly.
        """
        if not self.written:
            return
        self.stdout.write('\nrows rewritten:')
        for (ancestor, descendant), (statements, rows) in sorted(self.written.items()):
            live = apps.get_model('locations', descendant).objects.count() or 1
            self.stdout.write('  {:>14} -> {:<14} {:>12} rows in {:>6} statement(s)'
                              '  ({:.2f} per row)'.format(
                                  ancestor, descendant, rows, statements, rows / live))

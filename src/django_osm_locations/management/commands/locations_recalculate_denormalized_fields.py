from datetime import datetime

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, connection
from django.utils.text import slugify
from ...utils.progress import progress
from ...utils.bulk_writes import relax_durability
from ...utils.query_log import stop_logging_queries
from ...utils.recalc_scope import RecalcScope, assigned

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
    #: Which descendants each ancestor is pushed onto by containment.
    #:
    #: Address and Street are absent from the top levels on purpose. Both used
    #: to be in all four, and measured on this data a street was rewritten 7.01
    #: times per row and an address 3.89 -- because a row in a place is in that
    #: place's country and country subarea by construction, so those passes were
    #: computing what a join already knew. inherit_address_ancestry_from_place
    #: and inherit_street_ancestry_from_place do the joins; after the first of
    #: them an address is rewritten 1.00 times. Their place subarea stays a
    #: containment test, because there is nothing above a place subarea to
    #: inherit from.
    DENORMALIZED_MODEL_CLASSES_TO_UPDATE_DICT = {
        'Country': [CountrySubarea, Place, PlaceSubarea],
        'CountrySubarea': [CountrySubarea, Place, PlaceSubarea],
        'Place': [Place, PlaceSubarea, Street],
        'PlaceSubarea': [PlaceSubarea, Street, Address],
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
            array_cat({field_name}, ARRAY[ARRAY[%(name)s, %(slug)s]]::varchar[]) WHERE
              ST_Intersects(center, (SELECT border FROM "{ancestor_table}" WHERE id = %(id)s)){same_level}{scope}
            ;
        """.format(**{
            'model_name': model_name,
            'field_name': field_name,
            'ancestor_table': ancestor_table,
            'same_level': cls.sql_for_same_level(model_name, ancestor_table),
            'scope': RecalcScope.sql(model_name, '"locations_{}"'.format(model_name)),
        })

    @staticmethod
    def sql_for_same_level(model_name, ancestor_table):
        """
        What a level pushed onto itself also has to check.

        A country subarea is pushed onto the country subareas inside it, a
        place onto places, a place subarea onto place subareas, and the test is
        only that the descendant's centre lies in the ancestor's border. Every
        row's centre lies in its own border, so every one of them listed itself
        as its own ancestor: Kazimierz under Kazimierz, "powiat krakowski" at
        the end of its own path. And a centre can lie in something smaller: the
        middle of powiat krakowski, which rings Kraków, is in gmina Zabierzów,
        so the gmina became the powiat's ancestor. An ancestor on the same
        level is a different row, and larger.
        """
        if ancestor_table != 'locations_{}'.format(model_name):
            return ''
        return (
            '\n              AND id <> %(id)s'
            '\n              AND coalesce(area, 0) < coalesce('
            '(SELECT area FROM "{}" WHERE id = %(id)s), 0)'.format(ancestor_table)
        )

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
                 AND ST_Intersects(d2.center, anc.border){not_merged}{same_level}
                GROUP BY d2.id
            ) AS a
            WHERE d.id = a.id;
        """.format(**{
            'model_name': model_name,
            'field_name': field_name,
            'ancestor_table': ancestor_table,
            # as in the per-ancestor version: a merged duplicate is not an
            # ancestor, and a level is not its own
            'not_merged': ('' if ancestor_table == 'locations_country'
                           else '\n                 AND NOT anc.is_merged'),
            'same_level': ('' if ancestor_table != 'locations_{}'.format(model_name) else
                           '\n                 AND anc.id <> d2.id'
                           '\n                 AND coalesce(d2.area, 0) < coalesce(anc.area, 0)'),
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

        # Merged rows are duplicates the import resolved -- the same district
        # drawn twice -- and kept only so a re-import can match them. Pushed
        # down too, they put every street of Kazimierz under Kazimierz twice.
        ancestors = model_class_to_update.objects.exclude(border=None)
        if hasattr(model_class_to_update.objects, 'not_merged'):
            ancestors = ancestors.filter(is_merged=False)
        ancestors = RecalcScope.filter(ancestors)
        objects = ancestors.only('name', 'slug').iterator()
        objects_count = ancestors.count()
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
            ancestor_started = datetime.now()
            ancestor_rows = 0
            for denormalized_model_class in denormalized_model_classes:
                field_name = model_class_to_update.__name__.lower()
                model_name = denormalized_model_class.__name__.lower()
                sql = cls.get_sql_for_update_denorm_fields(
                    model_name, field_name, model_class_to_update._meta.db_table,
                )
                with connection.cursor() as cursor:
                    cursor.execute(sql, {'name': obj.name, 'slug': obj.slug, 'id': obj.id})
                    cls._tally(model_class_to_update, denormalized_model_class,
                               cursor.rowcount)
                    ancestor_rows += max(cursor.rowcount, 0)
            # Per ancestor, not only per level: at this level there are three
            # countries and the United Kingdom's border carries 2.2 million
            # vertices against Poland's 103,000, so a level total cannot say
            # which of them the time went on. Printed only for the levels small
            # enough for it to be readable -- 3,040 country subareas would bury
            # the log.
            if objects_count <= cls.REPORT_EACH_ANCESTOR_UP_TO and report:
                report('    {} ({} vertices): {} rows in {}'.format(
                    obj.name, cls._border_vertices(model_class_to_update, obj.id),
                    ancestor_rows, datetime.now() - ancestor_started))

    #: rows rewritten per (ancestor model, descendant model), so a pass can say
    #: where its writes went rather than only how long it took. A descendant
    #: rewritten once per matching ancestor shows up here as a multiple of its
    #: row count, which is the thing worth noticing.
    written = None

    #: Above this many ancestors a per-ancestor line is noise, not information.
    REPORT_EACH_ANCESTOR_UP_TO = 20

    @classmethod
    def _border_vertices(cls, model_class, pk):
        """How complex this ancestor's border is -- the cost driver per row."""
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT ST_NPoints(border) FROM "{}" WHERE id = %s'.format(
                    model_class._meta.db_table), [pk])
            row = cursor.fetchone()
        return row[0] if row and row[0] is not None else 0

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

    #: Fields left as they are when the arrays are emptied, per model.
    #:
    #: An address's country is indexed (migration 0042), so emptying it made
    #: every one of a country's addresses a non-HOT update of all seventeen of
    #: the table's indexes -- and the inheritance step then wrote it back,
    #: the same again. For Germany's 20.6 million addresses the two took
    #: 4h57m and over 12h. Nothing needs it emptied: the inheritance step
    #: overwrites it for every address inside a place, a fresh import has it
    #: empty already, and an address outside every place keeps what the last
    #: run worked out for it. Its place is still emptied -- inheritance
    #: appends to that one.
    KEPT_ON_CLEAN = {'Address': ('country',)}

    @classmethod
    @transaction.atomic
    def clean_denorm_fields_before_update(cls):
        """
        Empty every denormalised array before the walk fills them again.

        These used to be set to a placeholder, [['', '']], because the walk
        appended each ancestor as a one-dimensional pair, and appending that to
        an empty array gives a one-dimensional result rather than a first row.
        The placeholder made the array two-dimensional from the start, and a
        whole pass at the end rewrote every row again to take it back out --
        1h28m of the last recalculation, most of it on 11 million addresses.

        Every append is a two-dimensional row now, ARRAY[ARRAY[name, slug]],
        and PostgreSQL makes an empty array into a one-row matrix from that
        directly. So empty is enough, and nothing has to be stripped.
        """
        for model_class_to_clean in cls.MODEL_CLASSES_TO_CLEAN:
            print("{} - cleaning denorm fields before update...".format(model_class_to_clean.__name__))
            kept = cls.KEPT_ON_CLEAN.get(model_class_to_clean.__name__, ())
            update_dict = {}
            for field_name in model_class_to_clean.denorm_fields:
                if field_name not in kept:
                    update_dict[field_name] = []
            if not update_dict:
                continue

            if hasattr(model_class_to_clean, 'objects_all'):
                model_class_to_clear_all = model_class_to_clean.objects_all
            else:
                model_class_to_clear_all = model_class_to_clean.objects

            # Only rows with something to empty. A country fresh from its
            # import has nothing in these yet, and rewriting every row anyway
            # -- each one through every index on the table -- kept the
            # Netherlands' 9.9 million addresses busy for over an hour to
            # change none of them.
            RecalcScope.filter(model_class_to_clear_all.all()).exclude(
                **update_dict
            ).update(
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
    #: How close an address has to be to a street of its name, measured to the
    #: street's line.
    #:
    #: It used to be measured to the street's centre, which is one point in the
    #: middle of what can be a road several kilometres long: most addresses on
    #: a long street are further than this from its middle, so they never
    #: linked. On a 1% sample of Polish and Danish addresses, 36.3% linked by
    #: centre and 92.5% by line. The settlement vote had the same flaw -- the
    #: same sample of streets drew 29,088 votes by centre and 73,757 by line,
    #: and 12% more streets got a settlement from their addresses rather than
    #: from the nearest-place guess.
    ADDRESS_MATCH_METRES = 100

    #: A box, in degrees, around which to look before matching by name.
    #:
    #: Matching an address to a street by name first means matching it against
    #: every street of that name in the country: 7,964 streets called Polna
    #: against 49,368 addresses on one, 393 million pairs for that name alone,
    #: each paying for a geography distance -- and the planner put the whole
    #: settlement query at 5.7 billion cost units. Looking in a small box first
    #: lets the GiST index on the address centre do the narrowing, and the name
    #: and exact distance then check a few dozen candidates: 27.9 million, a
    #: 200th of it.
    #:
    #: 0.005 degrees must contain ADDRESS_MATCH_METRES in every direction, or
    #: the box would drop genuine matches. It is 556m north-south everywhere,
    #: and east-west still 181m at 71 degrees north, the top of Norway -- so
    #: nothing within 100m is lost anywhere this package imports.
    MATCH_BOX_DEGREES = 0.005

    @classmethod
    def get_sql_for_address_street_link(cls):
        """
        Point each address at the street it names, and give it that ancestry.

        The match is by name within a settlement, because that is what an
        address states: addr:street says which street, addr:city or addr:place
        says which settlement. Geometry only breaks ties -- the nearest street
        of that name, within ADDRESS_MATCH_METRES.

        An address that no place contains takes its country, country subarea
        and place from that street. Containment is how an address gets them,
        and a village is a place node with no border, so an address in one is
        contained by nothing: 1,635,466 of Poland's 8.5 million addresses,
        418,762 of Austria's, had no country at all, and the per-country index
        -- which picks rows by country -- left every one of them out. The
        street has an answer for all three: its own, or failing that the
        settlement its addresses name, or the nearest place in its country.
        """
        unplaced = '{} IS NULL'.format(assigned('a'))
        return """
            UPDATE "locations_address" AS a SET
                street = x.pair,
                country = CASE WHEN {unplaced} THEN x.country ELSE a.country END,
                countrysubarea = CASE WHEN {unplaced} THEN x.countrysubarea
                                      ELSE a.countrysubarea END,
                place = CASE WHEN {unplaced} THEN x.place ELSE a.place END
            FROM (
                SELECT a2.id, ARRAY[ARRAY[s.name, s.slug]]::varchar[] AS pair,
                       s.country, s.countrysubarea, s.place
                -- OFFSET 0 keeps the filter inside: without it the planner
                -- ran the lookup below for every row of the table and joined
                -- the scope afterwards -- 16 million addresses walked for
                -- Poland's 8.5 million.
                FROM (
                    SELECT * FROM "locations_address" a2
                    WHERE a2.street_name <> '' AND a2.center IS NOT NULL{scope}
                    OFFSET 0
                ) a2
                CROSS JOIN LATERAL (
                    SELECT name, slug, country, countrysubarea, place
                    FROM "locations_street"
                    WHERE NOT is_merged
                      AND lines && ST_Expand(a2.center, {box})
                      AND name = a2.street_name
                      AND lines IS NOT NULL
                      AND ST_DWithin(lines::geography, a2.center::geography, %s)
                    ORDER BY lines <-> a2.center
                    LIMIT 1
                ) s
            ) AS x
            WHERE a.id = x.id;
        """.format(box=cls.MATCH_BOX_DEGREES, scope=RecalcScope.sql('address', 'a2'),
                   unplaced=unplaced)

    #: How many of the nearest places an address outside every place looks
    #: through for one in its own country. Near a border the nearest few can
    #: all be across it; 32 is a wide margin for that, and still an index
    #: walk rather than a scan.
    NEAREST_PLACES_CONSIDERED = 32

    @classmethod
    def get_sql_for_address_outside_places(cls):
        """
        Place the addresses neither a place nor a street placed.

        Those with no street name, or one no street within reach bears: a
        farm, a hamlet's houses along an unnamed track. The country is the one
        whose border contains the address, tested against the borders cut into
        tiles; the place is the nearest one in that country, within
        NEAREST_PLACE_KM, as for a street no place contains; the country
        subarea is that place's. An address beyond any place in reach still
        gets its country, so the country's index carries it.
        """
        return """
            UPDATE "locations_address" AS a SET
                country = ARRAY[ARRAY[x.country_name, x.country_slug]]::varchar[],
                countrysubarea = coalesce(x.countrysubarea, a.countrysubarea),
                place = coalesce(x.place, a.place)
            FROM (
                SELECT a2.id, c.name AS country_name, c.slug AS country_slug,
                       p.countrysubarea,
                       ARRAY[ARRAY[p.name, p.slug]]::varchar[] AS place
                FROM (
                    SELECT * FROM "locations_address" a2
                    WHERE {unplaced} AND a2.center IS NOT NULL{scope}
                    OFFSET 0
                ) a2
                CROSS JOIN LATERAL (
                    SELECT t.country_id FROM "address_country_tiles" t
                    WHERE t.geom && a2.center AND ST_Intersects(t.geom, a2.center)
                    LIMIT 1
                ) t
                JOIN "locations_country" c ON c.id = t.country_id
                -- The nearest places first, by the spatial index alone, and
                -- the country and the distance tested on those. Put in the
                -- same WHERE, the country let the planner answer from the
                -- country index instead: every place in the country read and
                -- sorted for each address, 20 addresses a second for Austria's
                -- 22,830 places and hopeless for Poland's 533,820.
                LEFT JOIN LATERAL (
                    SELECT near.name, near.slug, near.countrysubarea
                    FROM (
                        SELECT pl.name, pl.slug, pl.countrysubarea, pl.country, pl.center
                        FROM "locations_place" pl
                        WHERE pl.center IS NOT NULL AND NOT pl.is_merged
                        ORDER BY pl.center <-> a2.center
                        LIMIT {nearest}
                    ) near
                    WHERE {place_country} = c.slug
                      AND ST_DWithin(near.center::geography, a2.center::geography, %s)
                    ORDER BY near.center <-> a2.center
                    LIMIT 1
                ) p ON true
            ) AS x
            WHERE a.id = x.id;
        """.format(unplaced='{} IS NULL'.format(assigned('a2')),
                   place_country=assigned('near'), nearest=cls.NEAREST_PLACES_CONSIDERED,
                   scope=RecalcScope.sql('address', 'a2'))

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
                          ON a.center && ST_Expand(st.lines, {box})
                         AND a.street_name = st.name
                         AND a.settlement_name <> ''
                         AND ST_DWithin(a.center::geography, st.lines::geography, %s)
                        WHERE NOT st.is_merged AND st.center IS NOT NULL
                          AND st.lines IS NOT NULL{scope}
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
                -- By name first, then distance: most place names belong to a
                -- handful of places, so reading those few through the name
                -- index beats walking the spatial index outward until the
                -- name turns up. ST_Distance rather than the KNN operator,
                -- because the operator would steer the planner back to the
                -- spatial walk.
                CROSS JOIN LATERAL (
                    SELECT name, slug
                    FROM "locations_place" p
                    WHERE p.name = ranked.settlement_name
                      AND NOT p.is_merged AND p.center IS NOT NULL
                    ORDER BY ST_Distance(p.center, st2.center)
                    LIMIT 1
                ) p
            ) AS x
            WHERE s.id = x.street_id;
        """.format(box=cls.MATCH_BOX_DEGREES, scope=RecalcScope.sql('street', 'st'))

    #: A street with no place yet, in any of the shapes the array can take.
    PLACELESS = """(s.place IS NULL
                   OR array_length(s.place, 1) IS NULL
                   OR s.place = ARRAY[ARRAY['','']]::varchar[])"""

    @classmethod
    def get_sql_for_placeless_street_country(cls):
        """
        Give a street that is in no place its country, by its own border.

        A street takes its country from its place, and a place is inside its
        country by construction -- except where the place was only the nearest
        one, which the fallback below chooses from up to 25km away. Near a
        border that can be across it, and the street would inherit the wrong
        country. So the streets that will take the fallback are placed in a
        country first, by containment, and the fallback then looks only
        within that country.

        Only those streets: everything else already has a place, and testing
        all 2.8 million against the country borders is the pass inheritance
        exists to avoid.
        """
        # Against the borders cut into tiles (SQL_FOR_COUNTRY_TILES), not
        # the borders whole: a join on the whole borders tested each street
        # against every country in turn, the prepared-geometry cache never
        # held, and Britain's 537,135-vertex border was re-read per street --
        # half an hour and counting for one country's strays.
        return """
            UPDATE "locations_street" AS s
            SET country = ARRAY[ARRAY[c.name, c.slug]]::varchar[]
            FROM (
                SELECT s2.id, t.country_id
                FROM "locations_street" s2
                CROSS JOIN LATERAL (
                    SELECT t.country_id FROM "address_country_tiles" t
                    WHERE t.geom && s2.center AND ST_Intersects(t.geom, s2.center)
                    LIMIT 1
                ) t
                WHERE {placeless2}
                  AND s2.center IS NOT NULL AND NOT s2.is_merged{scope}
            ) AS m
            JOIN "locations_country" c ON c.id = m.country_id
            WHERE s.id = m.id;
        """.format(placeless2=cls.PLACELESS.replace('s.', 's2.'),
                   scope=RecalcScope.sql('street', 's2'))

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
                -- OFFSET 0 keeps the filter inside: without it the planner
                -- ran the lookup below for every row of the table and joined
                -- the scope afterwards -- 16 million addresses walked for
                -- Poland's 8.5 million.
                FROM (
                    SELECT * FROM "locations_street" s2
                    WHERE s2.center IS NOT NULL{scope}
                      AND (s2.place IS NULL
                           OR array_length(s2.place, 1) IS NULL
                           -- an array holding only the old placeholder is an
                           -- empty one
                           OR s2.place = ARRAY[ARRAY['','']]::varchar[])
                    OFFSET 0
                ) s2
                CROSS JOIN LATERAL (
                    SELECT name, slug, center
                    FROM "locations_place" pl
                    WHERE center IS NOT NULL AND NOT is_merged
                      -- The nearest place in the street's own country, where
                      -- the street has one: without this a street near a
                      -- border takes a place across it and inherits that
                      -- country. See get_sql_for_placeless_street_country.
                      AND (array_length(s2.country, 1) IS NULL
                           OR pl.country[1][2] = s2.country[1][2])
                    ORDER BY center <-> s2.center
                    LIMIT 1
                ) p
                WHERE ST_DWithin(p.center::geography, s2.center::geography, %s)
            ) AS x
            WHERE s.id = x.id;
        """.format(scope=RecalcScope.sql('street', 's2'))

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

        Runs last, after the street link, because it reads the linked street
        from element [1].
        """
        # Only the rows whose slug actually changes. slug carries two indexes,
        # so a rewrite of it can never be HOT: every one maintains all
        # seventeen indexes on the table. Writing all 11 million rows took
        # 2h02m, a quarter of the whole recalculation, and on a re-run of the
        # same data almost none of them change.
        return """
            UPDATE "locations_address" AS a SET slug = n.slug
            FROM (
                -- left(): a street slug near the limit plus a house number
                -- runs past the column
                SELECT id, left(CASE
                    WHEN street[1][2] IS NOT NULL AND street[1][2] <> ''
                        THEN slugify(street[1][2] || '-' || house_number)
                    WHEN place[1][2] IS NOT NULL AND place[1][2] <> ''
                        THEN slugify(place[1][2] || '-' || house_number)
                    ELSE slugify(name)
                END, 255) AS slug
                FROM "locations_address" a0
                WHERE true{scope}
            ) AS n
            WHERE a.id = n.id AND a.slug IS DISTINCT FROM n.slug;
        """.format(scope=RecalcScope.sql('address', 'a0'))

    @classmethod
    @transaction.atomic
    def update_address_slugs(cls):
        print("Address - deriving slugs from the linked street...")
        with connection.cursor() as cursor:
            cursor.execute(cls.get_sql_for_address_slugs())
            print("  {} derived".format(cursor.rowcount))

    #: What an address inherits from the place that contains it rather than
    #: working out for itself. Its place subarea is not here: those borders are
    #: small and there is nothing above them to inherit.
    ADDRESS_INHERITS = ('country', 'countrysubarea')

    @classmethod
    def get_sql_for_address_place_map(cls):
        """
        Which place contains each address, in one spatial join.

        This replaces three point-in-polygon passes over the whole address
        table with one. Measured on this data: pushing Place down onto 11
        million addresses one ancestor at a time is 27,993 statements at 1.88s
        each, extrapolating to 14h36m, and Country and CountrySubarea each cost
        another pass over the same rows against much larger borders.
        Aggregating the statement did not help -- a single statement for the
        level and batches of 2,000 ancestors both ran past generous timeouts --
        because the cost is the containment test itself, not the shape of the
        statement issuing it.

        An address inside a place is inside that place's country and country
        subarea by construction, so those two never needed testing. Only the
        place has to be found; everything above it is a join.

        No sort and no DISTINCT here: every match is kept, and choosing among
        them happens once the table is indexed, where it sorts two integers
        rather than eleven million wide rows.
        """
        return """
            DROP TABLE IF EXISTS "address_place";
            CREATE TEMPORARY TABLE "address_place" AS
            SELECT a.id AS address_id, p.id AS place_id, p.area AS place_area
            FROM "locations_address" a
            JOIN "locations_place" p
              ON p.border IS NOT NULL AND ST_Intersects(a.center, p.border)
            WHERE true{scope};
            CREATE INDEX "address_place_idx" ON "address_place" (address_id, place_area);
            ANALYZE "address_place";
        """.format(scope=RecalcScope.sql('address', 'a'))

    @classmethod
    def get_sql_for_address_inherited_ancestry(cls):
        """
        Copy the place's ancestry onto its addresses, and name the place.

        The smallest containing place wins. A point inside two places is a
        boundary anomaly rather than a fact about the world, and the more
        specific one is the better answer -- where the old pass appended both,
        ordered by size.

        The place's arrays are copied as they are: they hold only real
        ancestors, since the walk no longer writes a placeholder, so the copy
        is already in its final shape.
        """
        return """
            UPDATE "locations_address" AS d SET
                country = p.country,
                countrysubarea = p.countrysubarea,
                place = array_cat(d.place, ARRAY[ARRAY[p.name, p.slug]]::varchar[])
            FROM (
                SELECT DISTINCT ON (address_id) address_id, place_id
                FROM "address_place"
                ORDER BY address_id, place_area ASC NULLS LAST, place_id
            ) AS m
            JOIN "locations_place" p ON p.id = m.place_id
            WHERE d.id = m.address_id;
        """

    @classmethod
    def get_sql_for_street_inherited_ancestry(cls):
        """
        Copy the place's country and country subarea onto its streets.

        Same reasoning as for addresses, and the measurements say the same: a
        street was rewritten 7.01 times per row against an address's 1.00,
        because it still went through all four levels by containment while an
        address went through one. A street in a place is in that place's
        country and country subarea, so two of those four were computing what a
        join already knew.

        The street records its place by name and slug, and a slug is unique
        within a country, not across countries -- 84 place slugs are shared
        between Poland, Denmark and Britain, "centrum" and "berlin" between the
        first two. Joined on the slug alone, an UPDATE ... FROM that matched two
        places took whichever came first: a run that did so left 674 streets
        outside Denmark labelled Danish, 55 outside Poland labelled Polish and
        183 outside Britain labelled British, and the per-country index picks
        its rows by exactly that label. So of the places bearing the slug the
        nearest one is taken, which is right however the street got its place
        -- the one containing it, the nearest namesake the vote chose, or the
        nearest place in its country the fallback chose; a namesake in another
        country is hundreds of kilometres further off.

        The slug is read from element 1 or 2 of the array, for data written by
        the older walk that still carried a placeholder at 1.
        """
        slug = "coalesce(nullif(s2.place[1][2], ''), s2.place[2][2])"
        return """
            UPDATE "locations_street" AS s SET
                country = m.country,
                countrysubarea = m.countrysubarea
            FROM (
                SELECT s2.id, pl.country, pl.countrysubarea
                FROM (
                    SELECT * FROM "locations_street" s2
                    WHERE {slug} IS NOT NULL AND s2.center IS NOT NULL{scope}
                    OFFSET 0
                ) s2
                CROSS JOIN LATERAL (
                    SELECT p.country, p.countrysubarea
                    FROM "locations_place" p
                    WHERE p.slug = {slug}
                      AND p.center IS NOT NULL
                    ORDER BY ST_Distance(p.center, s2.center), p.id
                    LIMIT 1
                ) pl
            ) AS m
            WHERE s.id = m.id;
        """.format(slug=slug, scope=RecalcScope.sql('street', 's2'))

    @classmethod
    @transaction.atomic
    def inherit_street_ancestry_from_place(cls):
        """One join, replacing two point-in-polygon passes over 2.8m streets."""
        started = datetime.now()
        print("Street - inheriting country and country subarea from its place...")
        with connection.cursor() as cursor:
            cursor.execute(cls.get_sql_for_street_inherited_ancestry())
            print("  {} streets in {}".format(
                cursor.rowcount, datetime.now() - started))

    @classmethod
    @transaction.atomic
    def inherit_address_ancestry_from_place(cls):
        """One spatial pass, then joins. See the two SQL builders for why."""
        started = datetime.now()
        with connection.cursor() as cursor:
            print("Address - finding the place that contains each address...")
            # No shared hash for this join. A parallel hash lives in the
            # dynamic shared memory under /dev/shm, and Germany's 20.6 million
            # addresses grew it past the container's 1GB in seven seconds:
            # "could not resize shared memory segment ... No space left on
            # device". Without it the workers still run in parallel, each
            # with its own private hash. SET LOCAL ends with the transaction.
            cursor.execute('SET LOCAL enable_parallel_hash = off')
            cursor.execute(cls.get_sql_for_address_place_map())
            cursor.execute('SELECT count(*) FROM "address_place"')
            matches = cursor.fetchone()[0]
            print("  {} address-place matches in {}".format(
                matches, datetime.now() - started))

            print("Address - inheriting country and country subarea from it...")
            inherited = datetime.now()
            cursor.execute(cls.get_sql_for_address_inherited_ancestry())
            print("  {} addresses in {}".format(
                cursor.rowcount, datetime.now() - inherited))

    #: Country borders cut into tiles, for testing many points against them.
    #: Norway's border is 1.95 million vertices; a tile is at most 256.
    SQL_FOR_COUNTRY_TILES = [
        'DROP TABLE IF EXISTS "address_country_tiles"',
        'CREATE TEMPORARY TABLE "address_country_tiles" AS '
        'SELECT id AS country_id, ST_Subdivide(border, 256) AS geom '
        'FROM "locations_country" WHERE border IS NOT NULL',
        'CREATE INDEX ON "address_country_tiles" USING gist (geom)',
        'ANALYZE "address_country_tiles"',
    ]

    @classmethod
    @transaction.atomic
    def place_addresses_outside_places(cls):
        print("Address - placing those no place or street placed...")
        started = datetime.now()
        with connection.cursor() as cursor:
            for statement in cls.SQL_FOR_COUNTRY_TILES:
                cursor.execute(statement)
            cursor.execute(cls.get_sql_for_address_outside_places(),
                           [cls.NEAREST_PLACE_KM * 1000])
            placed = cursor.rowcount
            cursor.execute('DROP TABLE "address_country_tiles"')
            print("  {} placed in {}".format(placed, datetime.now() - started))

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

            print("Street - placing the rest in a country by its border...")
            for statement in cls.SQL_FOR_COUNTRY_TILES:
                cursor.execute(statement)
            cursor.execute(cls.get_sql_for_placeless_street_country())
            print("  {} placed".format(cursor.rowcount))
            cursor.execute('DROP TABLE "address_country_tiles"')

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
        relax_durability(self.stdout)
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

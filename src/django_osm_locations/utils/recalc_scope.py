"""
Restricting a recalculation to one country.

The recalculation is global: adding a country, or re-importing one, meant
recalculating every country already in the database -- ten hours for Poland
and Denmark, and more with each country added -- although slugs are already
unique per country and nothing in Poland changes when France arrives.

A scoped run lists the rows that belong to one country in a table, once, and
every step of the walk restricts itself to them. What belongs to the country
is decided without a schema change, because the country a row is in is
already written on it by the previous run -- except on rows that have never
been through one, such as those of a fresh import. So a row is in scope when

* its country array already names the country, or
* its country array is empty and its position lies inside the country's
  border, or
* its country array is empty, it lies inside no border at all -- a pier's
  centre after the border was clipped to the coastline -- and this is the
  nearest country, within ORPHAN_DEGREES.

The last rule cannot pull in a neighbour's rows across a land border: those
lie inside the neighbour's border, which the second rule's counterpart for the
neighbour claims first, and the NOT EXISTS below excludes them here.
"""
from django.db import connection
from django.db.models.expressions import RawSQL

TABLE = 'locations_recalc_scope'

#: How far outside every border an unplaced row may be and still be claimed by
#: the nearest country: about 5km, enough for a pier or a bridge off a clipped
#: coastline, and nowhere near enough to reach across a country.
ORPHAN_DEGREES = 0.05

#: The point that places a row, per model. Centres are written by the areas
#: phase, which a scoped run restricts too, so a freshly imported row may not
#: have one yet; its geometry stands in.
PLACED_BY = {
    'countrysubarea': 'coalesce(t.center, ST_PointOnSurface(t.border))',
    'place': 'coalesce(t.center, ST_PointOnSurface(t.border))',
    'placesubarea': 'coalesce(t.center, ST_PointOnSurface(t.border))',
    'street': 'coalesce(t.center, ST_PointOnSurface(t.lines))',
    'address': 't.center',
}

#: The country already written on a row, from element 1 or 2 -- a database
#: recalculated by the older walk may still hold a placeholder at 1.
ASSIGNED = "coalesce(nullif(t.country[1][2], ''), t.country[2][2])"

#: A row no recalculation has placed yet.
UNPLACED = ("(t.country IS NULL OR cardinality(t.country) = 0 "
            "OR t.country = ARRAY[ARRAY['','']]::varchar[])")


class RecalcScope:
    """
    The rows of one country, while a scoped recalculation runs.

    Class-level state, like the rest of the recalculation commands, because
    the walk is a chain of classmethods called through call_command; there is
    no instance to carry it. Unset, every clause below is empty and the walk is
    exactly the global one.
    """

    #: The Country row being recalculated, or None for a global run.
    country = None

    @classmethod
    def active(cls):
        return cls.country is not None

    @classmethod
    def sql_for_members(cls, model_name, table, country_id):
        placed_by = PLACED_BY[model_name]
        return """
            INSERT INTO "{scope}" (model, id)
            SELECT '{model}', t.id FROM "{table}" t, "locations_country" c
            WHERE c.id = {country_id}
              AND {assigned} = c.slug
            UNION
            SELECT '{model}', t.id FROM "{table}" t, "locations_country" c
            WHERE c.id = {country_id} AND c.border IS NOT NULL
              AND {unplaced}
              AND {placed_by} && c.border
              AND ST_Intersects({placed_by}, c.border)
            UNION
            SELECT '{model}', t.id FROM "{table}" t, "locations_country" c
            WHERE c.id = {country_id} AND c.border IS NOT NULL
              AND {unplaced}
              AND {placed_by} && ST_Expand(c.border, {orphan})
              AND NOT EXISTS (
                  SELECT 1 FROM "locations_country" c2
                  WHERE c2.border IS NOT NULL
                    AND {placed_by} && c2.border
                    AND ST_Intersects({placed_by}, c2.border))
              AND ST_DWithin({placed_by}, c.border, {orphan})
              AND c.id = (
                  SELECT c3.id FROM "locations_country" c3
                  WHERE c3.border IS NOT NULL
                  ORDER BY ST_Distance(c3.border, {placed_by}), c3.id
                  LIMIT 1)
            ON CONFLICT DO NOTHING;
        """.format(scope=TABLE, model=model_name, table=table, country_id=int(country_id),
                   assigned=ASSIGNED, unplaced=UNPLACED, placed_by=placed_by,
                   orphan=ORPHAN_DEGREES)

    @classmethod
    def build(cls, country, models, report=None):
        """
        List the country's rows, and switch every clause on.

        UNLOGGED because it is derived and rebuilt at the start of every
        scoped run, so there is nothing to recover after a crash.
        """
        with connection.cursor() as cursor:
            cursor.execute('DROP TABLE IF EXISTS "{}"'.format(TABLE))
            cursor.execute(
                'CREATE UNLOGGED TABLE "{}" (model varchar(32) NOT NULL, id integer NOT NULL, '
                'PRIMARY KEY (model, id))'.format(TABLE))
            for model in models:
                name = model.__name__.lower()
                cursor.execute(cls.sql_for_members(name, model._meta.db_table, country.id))
                if report:
                    report('  {}: {} rows in {}'.format(model.__name__, cursor.rowcount, country.name))
            cursor.execute('ANALYZE "{}"'.format(TABLE))
        cls.country = country

    @classmethod
    def clear(cls):
        cls.country = None
        with connection.cursor() as cursor:
            cursor.execute('DROP TABLE IF EXISTS "{}"'.format(TABLE))

    @classmethod
    def sql(cls, model_name, alias):
        """
        ``AND <alias>.id IN (the scope)`` for raw SQL, or nothing when unscoped.

        The model name is written in literally rather than bound: these
        clauses go into statements that already carry bound parameters, and a
        %s here would shift them.
        """
        if not cls.active():
            return ''
        return ' AND {}.id IN (SELECT id FROM "{}" WHERE model = \'{}\')'.format(
            alias, TABLE, model_name)

    @classmethod
    def filter(cls, queryset):
        """The same restriction for an ORM queryset."""
        if not cls.active():
            return queryset
        model = queryset.model
        if model._meta.db_table == 'locations_country':
            return queryset.filter(pk=cls.country.pk)
        return queryset.filter(id__in=RawSQL(
            'SELECT id FROM "{}" WHERE model = %s'.format(TABLE),
            [model.__name__.lower()]))

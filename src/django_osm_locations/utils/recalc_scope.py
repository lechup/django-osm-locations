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
lie inside the neighbour's border, so the nearest border to them is at distance
zero, and the rule takes only rows whose nearest border is further than that.

Both geometric rules run against the borders cut into small tiles, not against
the borders themselves. Norway's clipped border has millions of vertices, and
every fresh row tested against it whole -- and measured to every country's
border in turn to find the nearest -- kept the scope building for half an hour
before it reached the streets. A tile is a few hundred vertices, found through
its own index.
"""
from django.db import connection
from django.db.models.expressions import RawSQL

TABLE = 'locations_recalc_scope'

#: Every country's border cut into tiles, for the duration of a build.
TILES = 'locations_recalc_border_tiles'

#: Vertices per tile: ST_Subdivide's own default, and a size at which a
#: point-in-polygon test is no longer worth measuring.
TILE_VERTICES = 256

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

#: A row no recalculation has placed yet: one whose ASSIGNED is null, which
#: covers a NULL array, an empty one and the old ['',''] placeholder alike.
#: Written in terms of ASSIGNED rather than the array's shapes so the index on
#: that expression (migration 0042) answers it too: listing the fresh rows of
#: a country otherwise read the whole 12GB address table.
UNPLACED = '({} IS NULL)'.format(ASSIGNED)


def assigned(alias):
    """ASSIGNED for a table aliased other than t."""
    return ASSIGNED.replace('t.', alias + '.')


def unplaced(alias):
    """UNPLACED for a table aliased other than t."""
    return UNPLACED.replace('t.', alias + '.')


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
        """
        The three rules as one INSERT, reading the tiles build() made.

        The third rule asks the tile index for the nearest tile: if it is at
        distance zero the row lies inside some border and the rule is not for
        it; otherwise the nearest tile's country, within ORPHAN_DEGREES, is the
        one that claims it.

        Its prefilter box is an uncorrelated subquery, so it is computed once.
        Written against a joined country row, ST_Expand(c.border) was
        evaluated per row -- Norway's 31MB border unpacked for every street.
        """
        placed_by = PLACED_BY[model_name]
        return """
            INSERT INTO "{scope}" (model, id)
            SELECT '{model}', t.id FROM "{table}" t, "locations_country" c
            WHERE c.id = {country_id}
              AND {assigned} = c.slug
            UNION
            SELECT '{model}', t.id FROM "{table}" t
            WHERE {unplaced}
              AND EXISTS (
                  SELECT 1 FROM "{tiles}" b
                  WHERE b.country_id = {country_id}
                    AND b.geom && {placed_by}
                    AND ST_Intersects(b.geom, {placed_by}))
            UNION
            SELECT '{model}', t.id
            FROM "{table}" t
            CROSS JOIN LATERAL (
                SELECT b.country_id, b.geom FROM "{tiles}" b
                ORDER BY b.geom <-> {placed_by}
                LIMIT 1
            ) n
            WHERE {unplaced}
              AND {placed_by} && (
                  SELECT ST_Expand(ST_Envelope(border), {orphan})
                  FROM "locations_country" WHERE id = {country_id})
              AND n.country_id = {country_id}
              AND NOT ST_Intersects(n.geom, {placed_by})
              AND ST_DWithin(n.geom, {placed_by}, {orphan})
            ON CONFLICT DO NOTHING;
        """.format(scope=TABLE, tiles=TILES, model=model_name, table=table,
                   country_id=int(country_id), assigned=ASSIGNED, unplaced=UNPLACED,
                   placed_by=placed_by, orphan=ORPHAN_DEGREES)

    @classmethod
    def sql_for_tiles(cls):
        return [
            'DROP TABLE IF EXISTS "{}"'.format(TILES),
            'CREATE TEMPORARY TABLE "{tiles}" AS '
            'SELECT id AS country_id, ST_Subdivide(border, {n}) AS geom '
            'FROM "locations_country" WHERE border IS NOT NULL'.format(
                tiles=TILES, n=TILE_VERTICES),
            'CREATE INDEX ON "{}" USING gist (geom)'.format(TILES),
            'ANALYZE "{}"'.format(TILES),
        ]

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
            for statement in cls.sql_for_tiles():
                cursor.execute(statement)
            for model in models:
                name = model.__name__.lower()
                cursor.execute(cls.sql_for_members(name, model._meta.db_table, country.id))
                if report:
                    report('  {}: {} rows in {}'.format(model.__name__, cursor.rowcount, country.name))
            cursor.execute('DROP TABLE "{}"'.format(TILES))
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

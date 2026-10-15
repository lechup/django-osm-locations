from django.db import migrations

#: The country a row is in, read from element 1 or 2 of its country array --
#: the same expression utils.recalc_scope.ASSIGNED and the per-country
#: reindex use, written out identically so the planner matches it.
EXPRESSION = "coalesce(nullif(country[1][2], ''), country[2][2])"

#: The tables large enough for a full read to hurt. Place and the subareas are
#: thousands of rows; these are millions.
TABLES = ['locations_address', 'locations_street', 'locations_place']


def create(table):
    return 'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{0}_country_slug" ON "{0}" (({1}))'.format(
        table, EXPRESSION)


def drop(table):
    return 'DROP INDEX CONCURRENTLY IF EXISTS "{}_country_slug"'.format(table)


class Migration(migrations.Migration):
    """
    Index the country each row is in.

    Rebuilding one country's search index, and listing one country's rows for
    a scoped recalculation, both asked which rows are in a country -- of an
    array nothing indexed. Each read the whole address table, 12GB, to find a
    country's share: Sweden's first address went out only once the scan
    reached the end of the table, where the rows imported last sit, and the
    scans pushed everything else out of the page cache while they ran.

    Concurrently, so an existing installation keeps serving while it builds;
    that needs a migration outside a transaction.
    """

    atomic = False

    dependencies = [
        ('locations', '0041_sweden_switzerland_austria'),
    ]

    operations = [
        migrations.RunSQL(create(table), drop(table)) for table in TABLES
    ]

"""
Leave room on the page for the ancestry rewrites.

The recalculation pushes each ancestor's name and slug down onto its
descendants, which means every street and every address is rewritten once for
each ancestor that contains it -- measured on Poland, Britain and Denmark,
43 million rewrites across 11 million addresses and 23 million across 2.8
million streets.

Those rewrites touch only the denormalised array columns, and no index covers
them, so PostgreSQL can apply them as HOT updates: the new row version goes on
the same page and not one of the seventeen indexes on locations_address is
touched. That needs free space on the page, and a table packed at the default
fillfactor of 100 has none -- so every rewrite moved to another page and
maintained all seventeen indexes instead. The address table grew from 5.6GB
after import to 19GB, and the street table to 34GB.

70 leaves room for roughly the number of rewrites a row actually receives.
It applies to pages written from now on, so an existing table needs VACUUM FULL
(which also removes the bloat) or a fresh import before it takes effect.
"""
from django.db import migrations

TABLES = ('locations_address', 'locations_street')


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0038_add_house_number_sort'),
    ]

    operations = [
        migrations.RunSQL(
            sql='ALTER TABLE "{}" SET (fillfactor = 70);'.format(table),
            reverse_sql='ALTER TABLE "{}" RESET (fillfactor);'.format(table),
        )
        for table in TABLES
    ]

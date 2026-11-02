"""
Remember which row a merged row went into.

Slugs of duplicates used to end in the OSM id -- ``station-road-25986049`` --
and links to them are out there. A row merged since is left out of the index,
and its OSM id with it; the row it went into now carries that id, and an old
link is redirected there. No constraint, as rows are merged in bulk.
"""
import django.db.models.deletion
from django.db import migrations, models

MERGEABLE = ('countrysubarea', 'place', 'placesubarea', 'street', 'address')


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0043_slugify_beyond_latin'),
    ]

    operations = [
        migrations.AddField(
            model_name=model_name,
            name='merged_into',
            field=models.ForeignKey(
                blank=True, db_constraint=False, default=None, null=True,
                on_delete=django.db.models.deletion.DO_NOTHING, related_name='+',
                to='locations.{}'.format(model_name),
                help_text='The location this one was merged into.'),
        )
        for model_name in MERGEABLE
    ]

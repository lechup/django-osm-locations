from django.db import migrations

# Frozen at the time this migration was written. Migrations must not read the
# fixture file: that file can change, and a migration has to keep producing the
# same result forever.
COUNTRIES = [
    {'name': 'Polska', 'slug': 'polska', 'iso2': 'PL', 'iso3': 'POL', 'un': 616},
    {'name': 'Deutschland', 'slug': 'deutschland', 'iso2': 'DE', 'iso3': 'DEU', 'un': 276},
    {'name': 'Isle of Man', 'slug': 'isle-of-man', 'iso2': 'IM', 'iso3': 'IMN', 'un': 833},
]


def seed_countries(apps, schema_editor):
    """
    Create the countries the importers look up by slug.

    Without these rows an import downloads its extract and only then fails with
    Country.DoesNotExist, which is a confusing way to be told to run loaddata.

    Matched on (iso2, iso3, un) -- the model's own uniqueness constraint -- so a
    database that already loaded the fixture keeps the rows it has, borders and
    all, rather than gaining duplicates.
    """
    Country = apps.get_model('locations', 'Country')
    for values in COUNTRIES:
        Country.objects.get_or_create(
            iso2=values['iso2'],
            iso3=values['iso3'],
            un=values['un'],
            defaults={'name': values['name'], 'slug': values['slug']},
        )


def unseed_countries(apps, schema_editor):
    """
    Deliberately does nothing.

    By the time anyone migrates backwards past this, these rows may carry an
    imported border and be referenced by every location in the database.
    Removing them to undo a seed would throw that away.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0030_align_slugify_with_anyascii'),
    ]

    operations = [
        migrations.RunPython(seed_countries, unseed_countries),
    ]

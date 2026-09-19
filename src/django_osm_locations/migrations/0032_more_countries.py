from django.db import migrations

# Frozen, like 0031. Slugs follow the existing convention of the country's own
# name for itself: polska, deutschland.
COUNTRIES = [
    {'name': 'Danmark', 'slug': 'danmark', 'iso2': 'DK', 'iso3': 'DNK', 'un': 208},
    {'name': 'Suomi', 'slug': 'suomi', 'iso2': 'FI', 'iso3': 'FIN', 'un': 246},
    {'name': 'Norge', 'slug': 'norge', 'iso2': 'NO', 'iso3': 'NOR', 'un': 578},
    {'name': 'Nederland', 'slug': 'nederland', 'iso2': 'NL', 'iso3': 'NLD', 'un': 528},
]


def seed_countries(apps, schema_editor):
    Country = apps.get_model('locations', 'Country')
    for values in COUNTRIES:
        Country.objects.get_or_create(
            iso2=values['iso2'],
            iso3=values['iso3'],
            un=values['un'],
            defaults={'name': values['name'], 'slug': values['slug']},
        )


def unseed_countries(apps, schema_editor):
    """Deliberately does nothing, for the reasons given in 0031."""


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0031_seed_countries'),
    ]

    operations = [
        migrations.RunPython(seed_countries, unseed_countries),
    ]

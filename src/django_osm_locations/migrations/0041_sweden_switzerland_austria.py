from django.db import migrations

# Frozen, like 0031 and 0032. Names are each country's own; Switzerland's is
# OSM's, in all four national languages.
COUNTRIES = [
    {'name': 'Sverige', 'slug': 'sverige', 'iso2': 'SE', 'iso3': 'SWE', 'un': 752},
    {'name': 'Schweiz/Suisse/Svizzera/Svizra', 'slug': 'schweiz-suisse-svizzera-svizra',
     'iso2': 'CH', 'iso3': 'CHE', 'un': 756},
    {'name': 'Österreich', 'slug': 'osterreich', 'iso2': 'AT', 'iso3': 'AUT', 'un': 40},
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
        ('locations', '0040_place_name_index'),
    ]

    operations = [
        migrations.RunPython(seed_countries, unseed_countries),
    ]

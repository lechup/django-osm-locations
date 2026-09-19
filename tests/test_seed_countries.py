"""
Countries are seeded by a migration.

Without them an import downloads its extract and only then fails with
Country.DoesNotExist, which is a confusing way to be told to run loaddata.
"""
import pytest

import importlib

from django_osm_locations.models import Country

MIGRATION = importlib.import_module(
    'django_osm_locations.migrations.0031_seed_countries'.replace('.0031', '.0031')
)

pytestmark = pytest.mark.django_db


def seed():
    """Run the migration's own function again, against the real models."""
    from django.apps import apps

    MIGRATION.seed_countries(apps, None)


class TestSeededRows:
    def test_the_originally_seeded_countries_are_present(self):
        """
        These are the ones migrations 0031 and 0032 create. Countries added
        since are created on first import from what their importer declares,
        so a new one does not need a migration and will not be here.
        """
        for slug in ('polska', 'deutschland', 'isle-of-man',
                     'danmark', 'suomi', 'norge', 'nederland'):
            assert Country.objects.filter(slug=slug).exists(), slug

    def test_every_importer_can_have_its_row_created(self):
        """
        What replaced the per-country migration: whatever the registry knows
        about, locations_import can bring into existence before downloading.
        """
        from django_osm_locations.management.commands.importers import registry
        from django_osm_locations.management.commands.locations_import import Command

        for importer in registry().values():
            Command.ensure_country(importer)
            assert Country.objects.filter(slug=importer.country_slug).exists(), \
                importer.country_slug

    @pytest.mark.parametrize('slug,iso2,iso3,un', [
        ('polska', 'PL', 'POL', 616),
        ('deutschland', 'DE', 'DEU', 276),
        ('isle-of-man', 'IM', 'IMN', 833),
    ])
    def test_codes_are_right(self, slug, iso2, iso3, un):
        country = Country.objects.get(slug=slug)
        assert (country.iso2, country.iso3, country.un) == (iso2, iso3, un)

    def test_no_duplicates(self):
        slugs = list(Country.objects.values_list('slug', flat=True))
        assert len(slugs) == len(set(slugs))


class TestReRunning:
    def test_seeding_again_creates_nothing(self):
        """
        It matches on the uniqueness constraint, so a database that already
        loaded the fixture keeps the rows it has rather than gaining copies.
        """
        before = Country.objects.count()
        seed()
        assert Country.objects.count() == before

    def test_seeding_again_does_not_overwrite_imported_data(self):
        """
        By the time this runs again these rows may carry an imported border.
        """
        from django.contrib.gis.geos import MultiPolygon

        from .conftest import square

        poland = Country.objects.get(slug='polska')
        poland.name = 'Renamed by hand'
        poland.border = MultiPolygon(square(0, 0, 5))
        poland.save()

        seed()

        poland.refresh_from_db()
        assert poland.name == 'Renamed by hand'
        assert poland.border is not None

    def test_the_reverse_keeps_the_rows(self):
        """
        Undoing the seed would throw away whatever has since been imported into
        those rows, so the reverse deliberately does nothing.
        """
        from django.apps import apps

        before = Country.objects.count()
        MIGRATION.unseed_countries(apps, None)
        assert Country.objects.count() == before

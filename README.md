# django-osm-locations

A Django app that imports OpenStreetMap extracts into a PostGIS location
hierarchy and serves them over a REST API.

It turns a `.pbf` country extract into five related models — countries, country
subareas, places, place subareas and streets — each carrying a border or
centreline geometry, a bounding box, a slug and denormalised ancestry, with
full-text search vectors for prefix matching.

## Requirements

- Python 3.9+
- Django 4.2+
- PostgreSQL with PostGIS (the app relies on `django.contrib.postgres` and
  GeoDjango; no other database backend will work)

## Installation

```bash
pip install django-osm-locations          # models, admin, middleware
pip install 'django-osm-locations[api]'   # + the REST API layer
```

Add it to your project:

```python
INSTALLED_APPS = [
    "django.contrib.gis",
    "django.contrib.postgres",
    "django_osm_locations",
    ...
]

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        ...
    }
}
```

```bash
python manage.py migrate
```

> **Upgrading from the submodule layout:** the app keeps the Django app label
> `locations`, so existing tables, migration history and content types are
> reused as-is. Replace `<project>.locations` with `django_osm_locations` in
> `INSTALLED_APPS` and update your imports; no data migration is required.
>
> **Reindex after upgrading.** Migration `0030` redefines the database-side
> `slugify()` so that it agrees with the Python one (see
> [Search normalisation](#search-normalisation)). Rows indexed by the old
> function keep their old search vectors until you rebuild them:
>
> ```bash
> python manage.py migrate
> python manage.py locations_recalculate_denormalized_fields
> ```
>
> Stored `slug` columns are unaffected.

## Models

| Model | Geometry | Notes |
|---|---|---|
| `Country` | `MultiPolygon` | ISO alpha-2/alpha-3 and UN codes |
| `CountrySubarea` | `MultiPolygon` | regions, voivodeships, counties |
| `Place` | `MultiPolygon` | cities, towns, villages, hamlets |
| `PlaceSubarea` | `MultiPolygon` | districts and neighbourhoods |
| `Street` | `MultiLineString` | ordered by length |

All but `Country` carry an `is_merged` flag: OSM frequently represents one
real-world place as several relations, ways and nodes, and the import pipeline
marks the redundant copies rather than deleting them. Use the manager helpers
`Model.objects.not_merged()` / `.merged()` to filter.

Ancestry is denormalised onto each row (`country`, `countrysubarea`, `place`,
`placesubarea`) as arrays of `[slug, name]` pairs, so a street row knows its
city and region without a join.

## REST API

Requires the `api` extra. Register the viewsets on your own router:

```python
from rest_framework import routers
from django_osm_locations.api.urls import update_router_with_locations

router = update_router_with_locations(routers.DefaultRouter())
urlpatterns = [path("api/", include(router.urls))]
```

This exposes `countries`, `country-subareas`, `places`, `place-subareas` and
`streets`, all read-only and looked up by slug. List responses omit the heavy
geometry; detail responses include it.

Filtering:

- `?search_vector_name=krak` — prefix search on the name
- `?search_vector=krak,mal` — prefix search across denormalised ancestry
- `?place=<slug>` / `?country_subarea=<slug>` — restrict to a parent's border
- `?in_bbox=<minx>,<miny>,<maxx>,<maxy>` — bounding-box filter

## Search normalisation

Three things have to agree or prefix search silently returns nothing:

| What | Produced by |
|---|---|
| the stored `slug` column | `django.utils.text.slugify(unidecode(name))` |
| `search_vector` / `search_vector_name` | the database `slugify()` function |
| the incoming query in `?search_vector_name=` | `django_osm_locations.utils.slugify` |

All three transliterate through **unidecode**, so `München` is stored, indexed
and searched as `munchen`. The database function is generated from the unidecode
table over Latin-1 Supplement and Latin Extended-A rather than maintained by
hand, so the two sides cannot drift apart.

Currency and typographic symbols (`¢ £ ° © µ`) are stripped rather than spelled
out; every letter in that range round-trips identically.

If you change one side, change all three and rerun
`locations_recalculate_denormalized_fields`.

## Subdomain middleware

Optional. Resolves `<place-slug>.example.com` to a `Place` and attaches it as
`request.place`:

```python
MIDDLEWARE = ["django_osm_locations.middleware.PlaceSubdomainMiddleware", ...]
ROOT_URL = "example.com"
```

## Importing OSM data

The importer needs the `importers` extra **and** a Python 3 compatible
`imposm.parser`:

```bash
pip install 'django-osm-locations[importers,diskcache]'
```

`imposm.parser` is deliberately not declared as a dependency. Upstream only ever
published 1.0.7 to PyPI, which is Python 2 only; the 1.0.8 port that works on
Python 3 was never released there, and PyPI forbids direct-URL dependencies in
published metadata. Install it yourself from whichever fork you trust:

```bash
pip install https://github.com/<fork>/imposm-parser/archive/<ref>.tar.gz
```

It also needs `protobuf-compiler` and `libprotobuf-dev` at the system level.

Then:

```bash
python manage.py locations_import poland
python manage.py locations_recalculate_all
```

`locations_import` downloads the extract from [Geofabrik](http://download.geofabrik.de/)
if it is not already present, imports relations, ways and nodes, then merges
duplicates. Use `--import-only` / `--merge-only` to run one half, and `--update`
to re-download.

Adding a country means adding an importer in
`django_osm_locations/management/commands/importers/` that subclasses
`BaseImporter` and overrides the `_is_relation_place`-style predicates for that
country's `admin_level` conventions — see `poland.py` and `germany.py`.

### Management commands

| Command | Purpose |
|---|---|
| `locations_import <country>` | Download, parse and import an extract |
| `locations_recalculate_all` | Run every recalculation below, in order |
| `locations_recalculate_areas_and_lengths` | Recompute `area` / `length` |
| `locations_recalculate_slugs` | Recompute slugs |
| `locations_recalculate_denormalized_fields` | Rebuild ancestry arrays and search vectors |
| `locations_clear_merged` | Delete rows flagged as merged |
| `locations_clear_not_in_country` | Delete rows outside the country border |
| `locations_clear_place_not_defined` | Delete orphans with no parent place |

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `LOCATIONS_DATA_DIR` | `<BASE_DIR>/osm_data` | Where `.pbf` extracts are stored |
| `LOCATIONS_CACHE_DIR` | `<BASE_DIR>` | Parent of the importer's cache dirs |
| `LOCATIONS_DISK_CACHE` | `False` | Use `diskcache` for the import cache |
| `LOCATIONS_FILE_CACHE` | `False` | Use a `shelve` file cache |
| `LOCATIONS_MEMCACHED_CACHE` | `False` | Use memcached |

Importing a full country holds every node, way and relation in a cache. The
default is an in-memory dict, which is fastest but needs a lot of RAM for a
large extract — switch to `LOCATIONS_DISK_CACHE` if memory is tight.

## License

See `LICENSE`.

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
| the stored `slug` column | `django.utils.text.slugify(anyascii(name))` |
| `search_vector` / `search_vector_name` | the database `slugify()` function |
| the incoming query in `?search_vector_name=` | `django_osm_locations.utils.slugify` |

All three transliterate through **anyascii**, so `München` is stored, indexed
and searched as `munchen`. The database function is generated from the anyascii
table over Latin-1 Supplement and Latin Extended-A rather than maintained by
hand, so the two sides cannot drift apart.

Currency and typographic symbols (`¢ £ ° © µ`) are stripped rather than spelled
out; every letter in that range round-trips identically.

If you change one side, change all three and rerun
`locations_recalculate_denormalized_fields`.

## Subdomain middleware

Optional. Resolves `<place-slug>.example.com` to a `Place`:

```python
MIDDLEWARE = ["django_osm_locations.middleware.PlaceSubdomainMiddleware", ...]
ROOT_URL = "example.com"
```

It sets two attributes on every request:

- `request.subdomain` — the slug, `None` on the apex domain and on reserved
  subdomains, or `False` for a host that is not under `ROOT_URL`
- `request.place` — the matching `Place`, or `None`. Always set, so views can
  read it unconditionally.

Host parsing is driven by `ROOT_URL` rather than by counting dots, so
multi-part TLDs (`example.co.uk`), ports and reserved names all behave:

| Host (`ROOT_URL = example.com`) | Result |
|---|---|
| `krakow.example.com` | place `krakow` |
| `example.com` | apex, no place |
| `www.example.com` | apex (reserved) |
| `a.b.example.com` | not a place |
| `evil.com` | 301 to `ROOT_URL` |

For local development point `ROOT_URL` at the dev host — `ROOT_URL =
"localhost:8000"` makes both `localhost:8000` and `krakow.localhost:8000` work.

Reserved subdomains default to `www`, `api`, `admin`, `static`, `media`,
`mail`; override with `LOCATIONS_RESERVED_SUBDOMAINS`.

## Importing OSM data

The importer needs the `importers` extra:

```bash
pip install 'django-osm-locations[importers]'
```

That is all — parsing is done by [pyosmium](https://osmcode.org/pyosmium/),
which ships binary wheels, so there is no compiler or protobuf toolchain to set
up.

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
| `LOCATIONS_CACHE_DIR` | `<BASE_DIR>` | Parent of on-disk index directories |
| `LOCATIONS_LOCATION_STORAGE` | `flex_mem` | libosmium node-location index type |
| `LOCATIONS_DISK_CACHE` | `False` | Shorthand for a disk-backed location index |
| `LOCATIONS_RESERVED_SUBDOMAINS` | `www`, `api`, `admin`, … | Subdomains never treated as a place slug |
| `LOCATIONS_WRITE_BATCH_SIZE` | `1000` | Rows per bulk insert/update during import |

### Memory during import

Node coordinates are the bulk of an extract, and libosmium holds them in its own
index rather than in Python. `LOCATIONS_LOCATION_STORAGE` picks which one:

| Value | Where | Use for |
|---|---|---|
| `flex_mem` (default) | RAM | up to country size |
| `sparse_file_array` | disk | large extracts, or tight memory |
| `dense_file_array` | disk | planet-scale, dense id ranges |

Setting `LOCATIONS_DISK_CACHE = True` selects `sparse_file_array` as the
default. Everything the importer keeps in Python — the relations it matched,
the ways it will turn into geometry, and tagged nodes — is a small fraction of
the file, because the tag filters run inside libosmium.

## Development

The models need PostGIS, so the suite ships a database:

```bash
pip install -e '.[dev]'
docker compose -f docker-compose.test.yml up -d
pytest
docker compose -f docker-compose.test.yml down -v
```

Tests that do not touch the database (slug normalisation, host parsing, the
importer tag predicates) run without the container:

```bash
pytest tests/test_slugify.py tests/test_middleware.py tests/test_importers.py
```

### The end-to-end import test

One test parses a real OSM extract and runs the whole pipeline into PostGIS.
It is opt-in, because it needs a ~6MB download and about 20 seconds:

```bash
curl -L -o /tmp/isle-of-man.osm.pbf \
    https://download.geofabrik.de/europe/isle-of-man-latest.osm.pbf
LOCATIONS_TEST_PBF=/tmp/isle-of-man.osm.pbf pytest -m pbf
```

The Isle of Man extract is the smallest Geofabrik country extract that still
has the full hierarchy — `admin_level` 6 sheadings, `admin_level` 8 parishes,
and a few thousand named streets. It covers the parts no unit test reaches:
multi-way boundary assembly, geometry construction and the merge pass.

## License

BSD 3-Clause — see [LICENSE](LICENSE).

Note that this covers the **code**, not the data. OpenStreetMap is published
under the [Open Database License](https://www.openstreetmap.org/copyright), so
a database you build by running `locations_import` is an ODbL-derived database
and carries that license's attribution and share-alike obligations, whatever
license this package uses.

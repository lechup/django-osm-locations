# django-osm-locations

A Django app that imports OpenStreetMap extracts into PostGIS, builds an
OpenSearch index from it, and serves search over that index.

It turns a `.pbf` country extract into five related models — countries, country
subareas, places, place subareas and streets — each carrying a border or
centreline geometry, a bounding box, a slug and denormalised ancestry.

**PostGIS is the source of truth.** The import writes there and nothing else
does; the search index is derived from it and rebuilt on demand. Nothing in this
package deletes rows from the database — a rebuild that finds fewer rows than
expected refuses to go live rather than quietly serving less data.

```
 .pbf ──import──▶ PostGIS ──reindex──▶ OpenSearch ──▶ REST API
                 (truth)              (alias swap)
```

## Requirements

- Python 3.9+
- Django 4.2+
- PostgreSQL with PostGIS (the app relies on `django.contrib.postgres` and
  GeoDjango; no other database backend will work)
- OpenSearch 2.x, to serve the API

## Installation

```bash
pip install django-osm-locations             # models, admin, middleware
pip install 'django-osm-locations[search]'   # + the OpenSearch index
pip install 'django-osm-locations[api]'      # + the REST API over it
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

Requires the `api` extra and a populated index. Wire it in:

```python
from django_osm_locations.api.urls import urlpatterns as locations_urls

urlpatterns = [path("api/", include(locations_urls))]
```

```python
LOCATIONS_OPENSEARCH_HOSTS = ["http://opensearch:9200"]
```

### Endpoints

| Endpoint | Purpose |
|---|---|
| `/api/search/` | search across every location type |
| `/api/places/`, `/api/streets/`, … | the same, restricted to one type |
| `/api/<type>/<slug>/` | one location, with full geometry |

Types are `countries`, `country-subareas`, `places`, `place-subareas`, `streets`.

### Parameters

| Parameter | Example | Meaning |
|---|---|---|
| `q` | `?q=krak` | prefix search on name and ancestry |
| `type` | `?type=places,streets` | restrict to types (search endpoint) |
| `within` | `?within=douglas` | only inside that ancestor slug |
| `bbox` | `?bbox=-5,54,-4,55` | `minlon,minlat,maxlon,maxlat` |
| `near` + `radius` | `?near=-4.4,54.1&radius=5km` | within a distance of a point |
| `order` | `?order=name` | `relevance` (default with `q`), `name`, `size` |
| `geometry` | `?geometry=true` | include full geometry in list results |
| `page`, `page_size` | `?page=2&page_size=50` | paging, max 200 per page |

### Responses

```json
{
  "total": 42,
  "page": 1,
  "page_size": 20,
  "results": [
    {
      "type": "places",
      "id": 17,
      "name": "Douglas",
      "slug": "douglas",
      "score": 8.4,
      "center": {"lon": -4.48, "lat": 54.15},
      "bbox": [-4.5, 54.1, -4.4, 54.2],
      "area": 0.0031,
      "population": 26218,
      "osm": {"node_id": null, "way_id": null, "relation_id": 1234},
      "ancestry": [{"level": "country", "slug": "isle-of-man", "name": "Isle of Man"}],
      "ancestry_slugs": ["isle-of-man"]
    }
  ]
}
```

Geometry is stored in the index as `geo_shape` and returned by the detail
endpoint, or by any list request with `?geometry=true`. It is excluded from
lists by default because a country border per hit is a lot of JSON.

Prefix matching is done with an edge n-gram analyser at index time, with
`asciifolding`, so `munch` and `münch` both find *München*.

## Building the index

```bash
python manage.py locations_index_status      # what the DB has vs what is live
python manage.py locations_reindex           # rebuild and swap
```

`locations_reindex` builds a fresh timestamped index per type, counts what it
wrote, compares that against the database, and only then moves the alias. If it
comes up short it refuses to promote and exits non-zero, leaving the previous
index serving. The superseded index is kept as a rollback path.

```bash
python manage.py locations_reindex --dry-run          # report only
python manage.py locations_reindex places streets     # selected types
python manage.py locations_reindex --no-promote       # build, inspect, swap later
python manage.py locations_reindex --tolerance 10     # allow a small shortfall
python manage.py locations_index_cleanup --keep 1     # drop older indexes
```

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
| `locations_reindex` | Rebuild the search index and swap the alias |
| `locations_index_status` | Compare database row counts with the live index |
| `locations_index_cleanup` | Remove superseded indexes no alias points at |

Nothing here removes rows from the database. Earlier versions shipped
`locations_clear_merged`, `locations_clear_not_in_country` and
`locations_clear_place_not_defined`, which deleted rows the import had flagged;
those are gone. Rows flagged as merged simply stay in PostGIS and are left out
of the index.

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `LOCATIONS_DATA_DIR` | `<BASE_DIR>/osm_data` | Where `.pbf` extracts are stored |
| `LOCATIONS_CACHE_DIR` | `<BASE_DIR>` | Parent of on-disk index directories |
| `LOCATIONS_LOCATION_STORAGE` | `flex_mem` | libosmium node-location index type |
| `LOCATIONS_DISK_CACHE` | `False` | Shorthand for a disk-backed location index |
| `LOCATIONS_RESERVED_SUBDOMAINS` | `www`, `api`, `admin`, … | Subdomains never treated as a place slug |
| `LOCATIONS_WRITE_BATCH_SIZE` | `1000` | Rows per bulk insert/update during import |
| `LOCATIONS_OPENSEARCH_HOSTS` | `['http://localhost:9200']` | Where OpenSearch lives |
| `LOCATIONS_OPENSEARCH_INDEX_PREFIX` | `locations` | Prefix for every index and alias |
| `LOCATIONS_OPENSEARCH_CLIENT_KWARGS` | `{}` | Extra client args: auth, TLS, timeouts |

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
docker compose -f docker-compose.test.yml up -d   # PostGIS + OpenSearch
pytest                 # unit and database tests
pytest -m search       # index and API integration tests
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

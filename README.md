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
| `admin_level` | `?admin_level=4,6` | only these OSM admin levels |
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

### Relevance

A text score alone does not know what kind of thing it matched or how big it
is, which on a country's worth of data puts the three districts named Warszawa
above the city of 1.8 million, and ties Kraków with the village of
Krakuszowice. Two multiplicative factors fix that:

- **type** — a place outranks a subdivision of one, and outranks the county
  sharing its name (Poland has both a city and a county called Gdańsk).
  Override with `LOCATIONS_TYPE_BOOSTS`.
- **size** — population where OSM provides it, area otherwise, both damped by a
  logarithm so size nudges the order rather than dictating it.

Both apply only when there is a `q`; without one, results are sorted by size
anyway.

## Updating to newer OSM data

One command does the whole cycle:

```bash
python manage.py locations_update isle-of-man
```

It reads the replication sequence recorded in the extract's own header, asks
the server how far ahead it is, downloads **only the change files since**,
merges them into the extract, reimports into PostGIS, recalculates the derived
fields and rebuilds the search index.

```bash
python manage.py locations_update isle-of-man --dry-run   # how far behind?
python manage.py locations_update isle-of-man --no-reindex
python manage.py locations_update isle-of-man --max-size 20480
```

A `.pbf` extract is a snapshot, not a changelog, so refreshing from the extract
alone means re-downloading all of it — 5.8 MB for the Isle of Man, several GB
for Germany. The daily diffs are 1–17 kB, and an unchanged day is 104 bytes.
`--max-size` caps how much is buffered per run, so an extract that is a long way
behind catches up over several runs.

The individual steps are still available if you want them separately:

```bash
python manage.py locations_import --update isle-of-man   # re-download in full
python manage.py locations_recalculate_all
python manage.py locations_reindex
```

Each step is safe to interrupt and rerun:

- **Import** upserts on the OSM id, so rerunning updates rows in place rather
  than duplicating them. It never deletes: a feature removed from OSM keeps its
  row in PostGIS until you remove it yourself.
- **Reindex** builds into a new index and only moves the alias once the document
  count matches the database. The previous index keeps serving until then, and
  is kept afterwards so the swap can be undone.

Because nothing is deleted, the database only grows. Rows that no longer exist
upstream stay searchable, so if an extract's coverage shrinks you will need to
decide what to do about the leftovers — this package will not guess.

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

## Progress output

Long phases — reading a `.pbf`, importing, reindexing — draw a progress bar on
stderr, so the command's stdout stays pipeable and carries only the phase
summary.

Bars are drawn **only when stderr is a terminal**. Redirected to a log file or
run from cron they are silent, because a bar is a stream of carriage returns:
unreadable in a file, and in cron it is stderr output from a job that had
nothing to say, which is how a nightly import starts sending mail.

To override the detection:

```bash
python manage.py locations_import poland --no-progress   # never draw
python manage.py locations_import poland -v 0            # same
```

```python
LOCATIONS_SHOW_PROGRESS = True    # draw even when redirected
LOCATIONS_SHOW_PROGRESS = False   # never draw
```

## Slugs

Slugs are public — places are served as subdomains — so duplicates are resolved
by *where a thing is* rather than by appending an opaque OSM id:

```
station-road-ramsey        street, qualified by its place
station-road-port-erin
church-street-peel
nowa-wies-krakow           place, qualified by the nearest larger place
```

The largest of a set keeps the bare slug, so `douglas` stays `douglas` and only
its namesakes are qualified. "Largest" means population where OSM provides it
(it rarely does — 6 of 90 places on the Isle of Man) and area otherwise. Rows
flagged as merged always rank last: they are duplicates the import already
resolved, and they are not in the search index, so they never take a readable
slug from a live row.

The qualifier is appended with a plain hyphen and no connector word, because
"near", "bei" and "koło" would each be right in only one language, while the
qualifier itself is a local name.

The cascade differs by type — streets and place subareas use their parent place,
places use the nearest larger place within about 110 km, country subareas use
their country. The OSM id remains as a last resort for what geography cannot
separate, such as two streets of the same name in the same town. On the Isle of
Man extract it is never needed: all 2008 streets get a readable slug.

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

### Supported countries

| Country | Divisions | Places | Below a place |
|---|---|---|---|
| Poland | 4, 6, 7 | 8 | 9, 10, 11 |
| Germany | 4–7 | 8 | 9, 10 |
| Denmark | 4 regions | 7 kommuner | 9, 10 |
| Norway | 4 fylker | 7 kommuner | 9 bydeler |
| Finland | 4 maakunnat, 7 seutukunnat | 8 kunnat | 9, 10, 11 |
| Netherlands | 4 provincies | 8 gemeenten | 9, 10 |
| Isle of Man | 6 sheadings | 8 parishes | — |
| United Kingdom | 4 nations, 5 metropolitan counties, 6 counties | 7 NI districts, 8 districts | 9, 10 civil parishes |
| France | 4 régions, 6 départements | 8 communes | 9, 10, 11 |

Those numbers are `admin_level` values, and they differ by country: a
municipality is level 7 in Denmark and Norway but level 8 in Finland and the
Netherlands. Each was read from that country's own extract rather than from the
wiki.

The United Kingdom is the clearest case for keeping the level. The UK is
`admin_level` 2 with `ISO3166-1` `GB`, while England, Scotland, Wales and
Northern Ireland are `admin_level` 4 with subdivision codes `GB-ENG`, `GB-SCT`,
`GB-WLS` and `GB-NIR` — countries constitutionally, subdivisions in the data.
They belong in country subareas, and so do Greater London and Herefordshire,
three tiers below them.

The level is **kept on the row** and indexed, because once several countries
share a database the model alone stops being enough to say what something is:
country subareas then hold English constituent countries, English counties,
Danish regions and Polish gminy all at once. `?admin_level=4` separates them,
and ordering by it walks the hierarchy from the top down.

### Adding a country

One file in `django_osm_locations/management/commands/importers/`, and nothing
else — no download table, no slug table, no migration, no Makefile edit:

```python
from .nordic import AdminLevelImporter


class SwedenImporter(AdminLevelImporter):
    geofabrik_name = 'sweden'          # the name in the URL, and on the command line
    country_name = 'Sverige'
    country_slug = 'sverige'
    iso2, iso3, un = 'SE', 'SWE', 752

    countrysubarea_levels = ('4',)     # län
    place_levels = ('7',)              # kommuner
    placesubarea_levels = ('9', '10')
```

`make import sweden` then works: the importer is discovered by walking the
package, the download URL comes from `geofabrik_name` and `geofabrik_path`, and
the `Country` row is created from the rest on first import.

Countries with quirks get their own class instead of subclassing
`AdminLevelImporter`: Germany has to read `de:place` to tell a city from a
region, and Poland merges a city with the county of the same name.

Read the levels from the country's own extract rather than the wiki. The survey
that produced the table above is a dozen lines of pyosmium over the relations,
and the conventions genuinely differ between neighbours.

### Extracts contain their neighbours

A Geofabrik extract is a rectangle, so Denmark's holds German Kreise and
Swedish kommuner, and Norway's reaches into Finland and Russia. The importer
finds the country's own `admin_level=2` relation — matched on its ISO code,
since names in an extract are local and often multilingual — and rejects
anything whose centre falls outside it. The country border is taken from that
relation too.

If the extract has no usable level-2 relation, nothing is filtered and
everything in the file is imported, which is the behaviour this replaced.

### Management commands

| Command | Purpose |
|---|---|
| `locations_import <country>` | Download, parse and import an extract |
| `locations_recalculate_all` | Run every recalculation below, in order |
| `locations_recalculate_areas_and_lengths` | Recompute `area` / `length` |
| `locations_recalculate_slugs` | Recompute slugs, qualifying duplicates by location |
| `locations_recalculate_denormalized_fields` | Rebuild ancestry arrays and search vectors |
| `locations_update` | Fetch OSM diffs, reimport, recalculate and reindex |
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
| `LOCATIONS_SHOW_PROGRESS` | auto | Force progress bars on or off |
| `LOCATIONS_TYPE_BOOSTS` | places > subareas > streets | Relative search weight per location type |
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

A `Makefile` wraps the common tasks; `make` on its own lists them.

```bash
make install            # pip install -e '.[dev]'
make test               # start the test stack, wait for it, run the suite
make test-search        # the OpenSearch integration tier
make test-pbf PBF=...   # the end-to-end import test
make check              # build the wheel and sdist, then twine check
```

The two stacks are separate and can run at once — the test one is tmpfs and
wiped every run, the local one is persistent and tuned for bulk imports:

```bash
make local-up                      # persistent PostGIS + OpenSearch
make process poland                # migrate, import, recalculate, reindex
make import denmark norway         # several at once
make index-status                  # database counts against the live index
make demo                          # the Leaflet page on :8000
make local-down                    # stop, keeping the data
make local-reset                   # stop and DELETE the data
```

Individual steps are available too: `make import`, `make update`, `make merge`,
`make recalculate`, `make reindex`, `make index-cleanup`, `make shell`,
`make dbshell`, `make bench`.


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

### Running a real import locally

The test stack keeps PostgreSQL's data in `tmpfs` so tests start from nothing
every time. That is wrong for importing an actual country — several gigabytes of
rows would sit in RAM, on top of the memory the parse already needs. Use the
persistent stack instead:

```bash
docker compose -f docker-compose.local.yml up -d
export DJANGO_SETTINGS_MODULE=local.settings

python -m django migrate          # also seeds the country rows
python -m django locations_import poland
python -m django locations_recalculate_all
python -m django locations_reindex
```

It runs under its own compose project name and its own ports (55433 and 59201),
so it coexists with the test stack. `down` keeps the data in named volumes;
`down -v` deletes it.

`local/settings.py` is a development harness, not a deployment example. It
reads `LOCATIONS_LOCAL_DB_*` and `LOCATIONS_LOCAL_OPENSEARCH_*` from the
environment, and puts extracts in `local/osm_data/`.

**Memory.** Parsing holds the node index in RAM. Measured on the real Geofabrik
extracts:

| Extract | Size | Peak RSS parsing |
|---|---|---|
| Isle of Man | 6 MB | 0.15 GB |
| Poland | 2.0 GB | 4.55 GB |
| Germany | 4.6 GB | ~10 GB (extrapolated) |

Set `LOCATIONS_DISK_CACHE = True` to spill the node index to disk instead, which
trades roughly 4 GB of RAM for the same in disk on a Poland-sized extract.

### Looking at the data

`local/demo/` is a small Leaflet page for seeing what is actually in the index.
Django serves it from the same origin as the API, so there is no CORS to
configure and no extra container:

```bash
docker compose -f docker-compose.local.yml up -d
DJANGO_SETTINGS_MODULE=local.settings python -m django runserver
```

Then open <http://127.0.0.1:8000/>. Type to search, filter by type, tick
"restrict to the visible map" to drive the query from the viewport, and click a
result to draw its geometry and read its attributes — area, population, centre,
bounding box, OSM id, and the ancestry it was indexed with.

Selecting something puts it in the URL, so a view can be linked to directly:
`#places/krakow`, `#streets/florianska-3989492`.

The page follows the operating system's light or dark setting. The button in
the corner cycles through follow-system, light and dark and remembers the
choice; an explicit choice wins over the system in either direction.

Basemap tiles come straight from OpenStreetMap, which needs no API key. In dark
mode the CSS inverts them so the map matches the panel, which avoids depending
on a dark basemap that does need one.

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

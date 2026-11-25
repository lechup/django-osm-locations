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
| `Address` | `Point` | house number, its street and its settlement |

All but `Country` carry an `is_merged` flag: OSM frequently represents one
real-world place as several relations, ways and nodes, and the import pipeline
marks the redundant copies rather than deleting them. Use the manager helpers
`Model.objects.not_merged()` / `.merged()` to filter.

Ancestry is denormalised onto each row (`country`, `countrysubarea`, `place`,
`placesubarea`, and `street` for addresses) as arrays of `[slug, name]` pairs,
so a street row knows its city and region without a join — and an address can
print its whole parent path, `Polska › pomorskie › Gdańsk › Łąkowa › Łąkowa 5`.

Every row also carries `names`, OSM's translations of its name keyed by
language. See [Localized names](#localized-names).

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

Types are `countries`, `country-subareas`, `places`, `place-subareas`,
`streets`, `addresses`.

### Parameters

| Parameter | Example | Meaning |
|---|---|---|
| `q` | `?q=krak` | prefix search on name and ancestry |
| `type` | `?type=places,streets` | restrict to types (search endpoint) |
| `exclude_type` | `?exclude_type=addresses` | leave these types out |
| `within` | `?within=douglas` | only inside that ancestor slug; `places:antwerpen` for an ancestor of that type alone |
| `bbox` | `?bbox=-5,54,-4,55` | `minlon,minlat,maxlon,maxlat` |
| `near` + `radius` | `?near=-4.4,54.1&radius=5km` | within a distance of a point |
| `prefer` | `?prefer=19.94,50.06` | rank what is nearer this point (`lon,lat`) higher; nothing is left out |
| `fields` | `?fields=suggest` | answer with these fields only: `suggest`, or a list such as `name,slug,center` |
| `admin_level` | `?admin_level=4,6` | only these OSM admin levels |
| `order` | `?order=name` | `relevance` (default with `q`), `name`, `size`, `number` |
| `lang` | `?lang=pl` | names in that language where OSM has one, ancestry included |
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
      "name": "Douglas",
      "slug": "douglas",
      "score": 8.4,
      "center": {"lon": -4.48, "lat": 54.15},
      "bbox": [-4.5, 54.1, -4.4, 54.2],
      "area": 0.0031,
      "population": 26218,
      "osm": {"node_id": null, "way_id": null, "relation_id": 1234},
      "ancestry": [{"level": "country", "slug": "isle-of-man", "name": "Isle of Man"}],
      "ancestry_slugs": ["isle-of-man"],
      "path": ["Isle of Man", "Douglas"]
    }
  ]
}
```

A location is identified by its `type`, its country and its `slug`: a slug
is unique within one country and one type, not across countries — `centrum`
is a place in Poland and in Denmark. That triple is what the detail endpoint
takes, `/<type>/<slug>/?within=<country-slug>`. There is no numeric `id`:
each country is built in a database of its own, so row ids repeat between
countries and would mean nothing outside one.

`path` is the ancestry and the location's own name ready to print, in the
requested `lang`. It collapses a name repeated by the next level: a city with
county rights is a county, a commune and a place all called Żory, three levels
in `ancestry` — each with its own slug, which is what `within=` filters on —
and once in `path`.

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

Names match as prefixes, so `krak` finds Kraków, and a word typed whole counts
for more than the same letters starting a longer word: `Lublin` is Lublin
before Lubliniec, and `Świdnicka Wrocław` is in Wrocław before Kąty
Wrocławskie. A translation typed whole — `Warsaw` — counts as the native name
typed whole. And the name of the place something is in counts above the rest
of its ancestry, since a city is often called what its region is: `Calle de
Granada 28, Madrid` is in Madrid, not in Leganés in the Comunidad de Madrid.
Among addresses the number typed comes first: `14`, then `14a`, then `142`.

`prefer=lon,lat` is where the person asking is. Of the hundred streets called
Floriańska the one in their city comes first, which no text can decide.
Distance takes at most 40% of a score — nothing within 5 km, half of that
40% at 55 km — so it decides between results otherwise alike and does not bury
one that is not: `Warszawa` typed in Kraków is still the capital. Unlike
`near`, it leaves nothing out.

`fields=suggest` answers with `type`, `slug`, `name`, `path`, `country`,
`center`, `bbox` and `score` — about 320 bytes a result against 1,150, the
rest being the ancestry three times over, the translations and the OSM ids.
`country` is the slug of the country the result is in, which with the type and
slug names it. Any other list of fields can be asked for by name.

A misspelt name is found too: `Warszwa` is Warszawa, and `Marszałkowska
Warszwa` the street there. That is a stage of its own, asked only when few
results have every word as typed and the best of them has one only as the
start of another word — `Szczecn` is the start of the village Szczecno, and
finds Szczecin beside it. A word may be one letter off, two from six letters
up, and its first letter is taken as typed. Addresses are not found misspelt:
the street is.

With one or two letters and no digit, addresses are left out of a search
across types: `Ma` is the start of some 400,000 house names in Poland and
Germany, and nobody means one yet. A search is given two seconds
(`LOCATIONS_SEARCH_TIMEOUT`); one that runs out answers with what it found
and `"timed_out": true`.

## Published indexes

Building a country from scratch costs about thirteen hours: downloading the
extract, importing it, clipping borders to the coastline, recalculating slugs
and ancestry, and indexing. If all you want is to search, a published snapshot
skips every step of it.

```bash
sha256sum -c SHA256SUMS                            # check what you downloaded
cat pl-2026-09-24.tar.gz.part* | tar -xzf - -C local/snapshots
make load-snapshot pl-2026-09-24                   # restore and alias in one go
make load-snapshot pl                              # or undated: the newest one unpacked
make load-snapshot us                              # every part of a country packed in parts: us, us-ca, us-tx ...
make snapshots pl                                  # what that directory holds
```

The cluster has to be started with `path.repo` pointing at the directory the
snapshots live under — `docker-compose.local.yml` does that — because
OpenSearch will not take a directory at call time.

Each country unpacks into a directory of its own, `local/snapshots/pl`, and
that is not tidiness. A filesystem repository keeps one manifest at its root
naming every snapshot in it, so two countries in one repository are not two
downloads: no subset of that directory restores either one alone. The
directory name is where `load-snapshot` looks, taken from the snapshot name;
`--in <directory>` overrides it if you unpacked somewhere else.

Loading waits for every shard to be copied back before it touches an alias,
printing its progress once a minute -- Germany takes about half an hour. A
newer pack of a country already loaded replaces the older one only then, so
the older one answers searches until the newer one can. A restore that stalls,
on a disk past OpenSearch's watermark for instance, stops with the cluster's
reason and leaves the aliases as they were.

**Which packs exist, and what built them.** The
[pack index](https://github.com/lechup/django-osm-locations/releases/tag/packs)
lists every published pack: how old its data is, the version of this package
that built it, the oldest version that can load it, and its size. A pack is
deleted, release and tag, once a newer one of its country is published, so
the index is the current pack of each country; one still up beside its
successor is listed apart. `packs.json` on the same release holds it as data. From 0.2.4 a pack also carries this inside
it, in its snapshot: `make snapshots pl` shows it, and `load-snapshot` refuses
a pack that needs a newer version than the one installed (`--ignore-version`
loads it regardless). Packs made before 0.2.4 say nothing and load as before;
every one of them loads with 0.2.0 or later.

**Countries are published separately and merge by alias.** Load Poland, then
Denmark, and both sit under the same aliases and are searched as one. That is
an ordinary OpenSearch alias over several indexes, so nothing in the query
layer knows about it.

Two things follow, and both matter:

* **Merge by alias, never by reindexing several countries into one index.** A
  document's `_id` is its PostGIS primary key, unique per country and not
  across them, so one country would overwrite the other.
* **An index alone is read-only.** `locations_update` and the
  `locations_recalculate_*` commands all work on PostGIS, which a published
  index does not carry. To update between releases, or to extend the data,
  run the import yourself — that is what the rest of this README is about.

To publish:

```bash
make snapshot ISO=pl                  # package the live Polish indexes
make pack pl-2026-09-24               # tar, split, checksum, and print the notes
```

`snapshot` first merges each index into one segment, with no deleted document
left in it: a document written again after indexing stays in its old segment,
deleted, and still counts in the term statistics the scores are built from.

`pack` splits at 1.9 GB because a GitHub release asset may not exceed 2 GB and
a country is larger than that — three countries came to 4.3 GB. It refuses a
directory holding more than one snapshot rather than quietly publishing both,
and prints the release notes, including the commands above, the version that
built the pack and the attribution the license asks for.

`snapshot` records in the pack which version made it and the oldest that can
load it, `PACKS_LOAD_WITH` in `django_osm_locations/__init__.py`. Raise that
with any change an older reader would get wrong, a mapping or the pack's
layout; the loader refuses a pack that asks for more than it is. After a pack
is published, `scripts/pack_index.py --publish` rebuilds the pack index from
the releases.

A pack built before a fix to the recalculation or the mapping can be repaired
without the database it came from: `scripts/repair-packs.sh` restores each
pack into an empty cluster, runs the `locations_repair_*` commands on its live
indexes, and packs it again under the same name. Each repair finds nothing to
do in a pack that has it already.

Published indexes are derived from OpenStreetMap and carry ODbL's attribution
and share-alike obligations — see [License](#license). That is separate from
this package's own BSD-3 licence, which covers the code.

### What the recalculation actually costs

Pushing ancestry down rewrites every descendant once for each ancestor that
contains it, and a rewrite in PostgreSQL is a new row version. Measured across
Poland, Britain and Denmark: 43 million rewrites over 11 million addresses, 23
million over 2.8 million streets. The tables end up mostly dead rows —
`locations_street` reached 34GB and `VACUUM FULL` took it back to 4.3GB, so 87%
of it was bloat.

Those rewrites touch only the denormalised arrays, which no index covers, so
they can be HOT updates: the new version goes on the same page and none of the
seventeen indexes on `locations_address` is touched. HOT needs free space on the
page, so the two big tables are created with `fillfactor = 70` (migration 0039).
An existing database needs a rewrite before that takes effect:

```bash
make dbshell
=> VACUUM FULL locations_street;    -- 34GB -> 4.3GB, seven minutes
=> VACUUM FULL locations_address;
```

Do not add an index over `country`, `countrysubarea`, `place`, `placesubarea` or
`street` on those tables without measuring a recalculation afterwards: it makes
every one of those rewrites non-HOT again, whatever the fillfactor. There is a
test that fails if one appears.

A row in a place is in that place's country and country subarea by
construction, so pushing those down by point-in-polygon computes what a join
already knows. Addresses and streets therefore inherit them from their place
instead of being tested against every ancestor's border. Measured on Poland and
Denmark, rewrites per row:

| table | before | after |
|---|---|---|
| `locations_address` | 3.89 | **1.00** |
| `locations_street` | 7.01 | expected ~3 |

The phase timings say the same thing. `ancestry: Country down` was 3h10m51s and
became 43m13s; `ancestry: CountrySubarea down` had never finished — eight hours
got 11.5% of the way — and finished in 1h13m48s.

Three shapes of the per-ancestor statement were measured first and none helped:
27,993 statements at 1.88s each extrapolates to 14h36m, one statement for the
whole level ran past 45 minutes, and batches of 2,000 past 25. The cost is the
containment test, not the statement issuing it, which is why the fix is to stop
doing the test rather than to issue it differently.

`locations_recalculate_all` reports what each phase cost, and the denormalised
pass reports rows rewritten per level with a per-row ratio — a ratio well above
1 means a level is rewriting the same rows repeatedly.

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

One country at a time, when indexes are published separately:

```bash
python manage.py locations_reindex --country poland
```

That builds `locations-streets-pl-<suffix>` from Poland's rows alone and adds
it to the aliases without disturbing any other country already under them. A
rebuild without `--country` still replaces everything.

```bash
python manage.py locations_reindex --dry-run          # report only
python manage.py locations_reindex places streets     # selected types
python manage.py locations_reindex --no-promote       # build, inspect, swap later
python manage.py locations_reindex --tolerance 10     # allow a small shortfall
python manage.py locations_index_cleanup --list       # every index, its size and status
python manage.py locations_index_cleanup              # what would be removed
python manage.py locations_index_cleanup --delete     # remove it
python manage.py locations_index_cleanup --rollback --country pl --apply
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

### A slug is unique within a country, not across them

This is deliberate, and it is what lets each country's index be built,
published and replaced on its own: importing Denmark cannot rename a Polish
street and break a URL somebody has already published.

The price is that a slug does not identify a row globally. With several
countries loaded together, one can name more than one place — measured across
Poland, Britain and Denmark, 60 slugs do, out of roughly 422,000 rows that
keep an unqualified slug. They are mostly not real names: cycle trails called
`jurassic-park` and `drop-zone`, and short Danish-English villages like `dale`
and `aller`. Collisions grow with the square of the country count and with
linguistic proximity, so a Scandinavian set will see more than this.

Where it happens the detail endpoint says so rather than picking one, because
the winner would otherwise be whichever shard answered first:

```
GET /api/streets/dale/
409 {"detail": "2 locations share the slug 'dale'. A slug is unique within a
      country, not across them; add ?within=<ancestor slug> to choose.",
     "candidates": [...]}

GET /api/streets/dale/?within=polska
200 {...}
```

With one country loaded this cannot happen at all.

Houses that shared a slug — the same number twice on a street that runs
through two villages, or in a village numbered without streets — each have
one of their own, the slug they shared and something that tells them apart:
`ostergade-12-rudkobing`, `ostergade-12-bagenkop`. Asked for the slug they
shared, the detail endpoint answers 409 with them as candidates, as it did.

Within a country a slug is unique per type, not across types: the city of
Antwerpen and the province around it are both `antwerpen`, so
`?within=antwerpen` is the province. `?within=places:antwerpen` is the city
alone; the type is one of `countries`, `country-subareas`, `places`,
`place-subareas`, `streets`.

## Addresses

An address is a house number and what it is a house number on. They are the
one thing here that knows where a street is rather than inferring it: 64% of
Poland's addressed features and 80% of Britain's name both their street and
their settlement, against 0.6% of roads carrying `addr:city` themselves.

```
GET /api/addresses/?within=lakowa-gdansk        # a street, in walking order
GET /api/search/?q=Łąkowa 5
```

Ordering is by house number by default for an addresses-only request, because
an address has no size worth sorting by. `?order=number` asks for it anywhere.
A keyword sorts `1, 10, 11, 2` and an integer cannot hold `12a` or `5/7`, so
the leading integer orders and the raw number breaks ties: `1, 2, 3, 3 B, 5/7,
10, 12, 12a`. Measured across seven extracts and 36 million addresses, at most
0.2% of any country has no leading digit — British units like `Unit 10`,
Finnish stairwells like `B 20` — and those sort last.

They rank below streets in search, so looking for a street name does not bury
it under every house on it.

An address is on the street it names: the nearest of that name within 100 m,
or failing that the one of that name in its own place, however far — a block
set back across a courtyard is still on its street. One name in one place is
one street, its pieces joined, so there is only ever one to choose. And a
house is one address however many times the extract has it — a building, the
shops in it, an address point beside it: one number on one street within
100 m. It stands where its building outlines are, or its points where it has
none.

Suggestions the way a map offers them — a street first, then its number —
are two requests:

```
GET /api/search/?q=florianska kr&exclude_type=addresses    # streets and places
GET /api/addresses/?within=florianska-krakow&q=14          # that street's 14, 14a, 140
```

What is typed around an address and is no word of a name is read for what it
is: `ul.` is dropped; `al.`, `os.` and `pl.`, and `Aleja`, `Osiedle` and `Plac`
typed whole, are required only while something has them, since a name leaves
them out as often as it carries them; a postcode (`31-019`, or `22299` beside a house number) ranks
the addresses that have it first, and a flat (`14/3`, `14 m. 3`) finds house
14 — or a house numbered `14/3`, ahead of it, where there is one. A letter
after the slash is no flat: `15/a` is the house of that number.

The words of a street's kind are read abbreviated in other languages as well:
`Berliner Str.` and `Hauptstr.` are Straße, `Abbey Rd` is Road, `bd
Haussmann` boulevard, `V.le Europa` viale, `Avda. de América` avenida. A query
does not say its country, so every language's abbreviations apply together
and none is required of a result: what one stands for only ranks. `St` is
left as typed, being street, saint and sankt. The table is in
`api/street_words.py`.

Addresses are read in a pass of their own rather than cached with everything
else: 62.6% of them are building outlines, whose node references come to 43.9
million for Poland, and holding those is enough to get a desktop session
killed. The pass re-reads the extract once the country border is known, so
each address is filtered as it arrives and nothing is kept.

## Localized names

OSM carries translations as `name:<lang>`, and this package keeps them:

```
GET /api/search/?q=deutschland&lang=pl      → "Niemcy"
GET /api/search/?q=belfast&lang=pl          → ancestry as "Wielka Brytania › …"
```

Anything without a translation keeps the name OSM stored, which is most
things: coverage runs opposite to row count. A country relation carries 200–270
translations and a voivodeship about 95, while 7–10% of places and 0.5% of
streets have any at all — so the rows people most want translated are the ones
that carry translations.

`name:genitive` is not a language. It is the most common `name:*` tag in the
Polish extract at 49,118 occurrences, ahead of every real one, and
`name:prefix`, `name:adjective` and `name:etymology` sit in the same
namespace; they are filtered out before anything is stored.

An address keeps its name in every language: it is named for its street and
number, and the translations on it are those of the shop or hotel mapped
there. Its ancestry is translated like any other.

The ancestors' names are looked up once and remembered for
`LOCATIONS_ANCESTOR_NAMES_CACHE_TIMEOUT` seconds in Django's default cache: an
ancestor's document is read whole to give them, border included, and asked on
every request that took a suggestion from 10 ms to 190 ms.

Translations are stored but not indexed field-by-field — a mapping field per
language would be 375 of them — and one `names_all` field makes them all
searchable, so "niemcy" finds Deutschland. Slugs stay monolingual: they are
identifiers, not labels.

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

### Old slugs

Slugs used to tell duplicates apart by their OSM id — `station-road-25986049`,
`wieliczka-1234567` — and links to those are in search engines. Optional, and
placed before the subdomain middleware so the 404 it raises reaches it:

```python
MIDDLEWARE = [
    "django_osm_locations.middleware.OldSlugRedirectMiddleware",
    "django_osm_locations.middleware.PlaceSubdomainMiddleware",
    ...
]
```

On a 404 it looks at every segment of the path shaped like `<slug>-<number>`
that is no slug of today's, whatever the project's URLs are, and answers 301
with the slugs the locations have now. The number is still on the location, or
on the one it was merged into, and the name in front of it says which object
of that number it is. When the object is gone from OSM, the name alone is
looked for under the place of the subdomain, and followed when one location
fits. An old place slug as a subdomain is resolved the same way, before the
subdomain middleware falls back to cutting the number off.

```python
from django_osm_locations.search.old_slugs import resolve_old_slug

resolve_old_slug("station-road-25986049")       # "station-road-ramsey"
resolve_old_slug("aleja-3-maja")                # None: a slug of today's
```

It reads the indexes, so it works with nothing but published packs, and keeps
each answer — found or not — for `LOCATIONS_OLD_SLUG_CACHE_TIMEOUT` seconds,
a day by default.

A merged row is not in the index; the one it went into carries its OSM id in
`osm.merged_node_ids`, `merged_way_ids` and `merged_relation_ids`. Indexes
built before that have none, and `locations_repair_merged_ids <country>
<extract.osm.pbf>` reads them back from the extract: what the importer takes
that the pack has no location for, matched by name to where it went.

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

### Hooks

Work that is specific to one country hangs off four hooks rather than an
override of the pipeline:

| hook | runs |
|---|---|
| `before_import` | after the extract is parsed, before anything is written |
| `after_import` | once every row exists and the country border is set |
| `before_merge` | at the start of the merge |
| `after_merge` | once merging has finished |

All four are no-ops, so there is nothing to chain and no `super()` to forget.
`before_import` sees everything parsed — relations, ways, nodes, the country
boundary — and `after_import` sees the rows, which is where to reshape them.

`PolandImporter.before_merge` is the worked example: Warszawa appears as *miasto
Warszawa*, *gmina miejska Warszawa* and *miasto stołeczne Warszawa*, and where a
country subarea has exactly the border of a place, the subarea is the redundant
one.

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
| `locations_index_cleanup` | List indexes, or remove superseded ones no alias points at (reports only without `--delete`) |
| `locations_load_land_polygons` | Load the coastline shapefile into PostGIS |
| `locations_clip_borders_to_land` | Trim borders back to that coastline |
| `locations_export_snapshot` | Package a country's live indexes for publication |
| `locations_import_snapshot` | Restore a published snapshot and alias it |
| `locations_repair_ancestry` | Repair the ancestry of indexes built before it was fixed |
| `locations_repair_search_fields` | Add the whole-word and whole-translation fields to older indexes |
| `locations_repair_addresses` | Join street pieces, link addresses, one address per house, in older indexes |
| `locations_repair_merged_ids <country> <extract>` | Give older indexes the OSM ids of their merged rows, from the extract |

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
| `LOCATIONS_ANCESTOR_NAMES_CACHE_TIMEOUT` | `3600` | Seconds a translated ancestor name is remembered; `0` asks the index each time |
| `LOCATIONS_SEARCH_TIMEOUT` | `'2s'` | What a search may take before it answers with what it has; `None` for no limit |
| `LOCATIONS_OPENSEARCH_HOSTS` | `['http://localhost:9200']` | Where OpenSearch lives |
| `LOCATIONS_OPENSEARCH_INDEX_PREFIX` | `locations` | Prefix for every index and alias |
| `LOCATIONS_OPENSEARCH_CLIENT_KWARGS` | `{}` | Extra client args: auth, TLS, timeouts |
| `LOCATIONS_LAND_POLYGONS_TABLE` | none | Table the coastline lives in; needed to clip borders |

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

A `*_file_array` index needs a file, and libosmium does not insist: given the
bare type name it maps anonymous memory instead and is as resident as
`flex_mem` while reading as though it were not. The importer therefore names
one itself, under `LOCATIONS_CACHE_DIR`, and removes it when the pass is done;
it is about 16 bytes a node, so tens of gigabytes for a large country. Name
your own with `LOCATIONS_LOCATION_STORAGE = "sparse_file_array,/path/to.idx"`
to put it on a particular filesystem.

Where to draw the line, measured on a 30GB desktop with an 8GB PostGIS beside
it: Poland (2GB extract) fits in memory, France (4.8GB) does not — in memory
its index grew twelve gigabytes in three minutes. On disk the same pass holds
around 200MB of private memory and the rest is page cache the kernel drops
under pressure.

### Do not let an import share a cgroup with your editor

`systemd-oomd` kills on memory *pressure* across a whole cgroup, and a
terminal opened inside an editor is in the editor's cgroup. An import that
overruns there does not die alone: it took VS Code, `dbus` and `pipewire` with
it, which is a logged-out desktop. `MemoryMin` does not help — it guards
against reclaim, while the trigger is pressure, and pressure is what a large
allocation makes.

What does help is a cgroup of its own with a cap, so the kernel kills the
import and nothing else. `make import` does this where a user systemd is
running; `IMPORT_LIMIT` sets the cap, 8G by default:

```bash
make import france IMPORT_LIMIT=6G

# by hand, for any of the long commands
systemd-run --user --scope -p MemoryMax=8G -- python manage.py locations_import france
```

A memory cap turns out to be the wrong instrument, and measurement is what
says so. `memory.max` counts page cache; the node index is written through
`mmap`, so its pages are dirty; and a cgroup sitting at its limit has to write
those back inside reclaim — scattered synchronous IO, which stalls, which *is*
pressure. Page cache expands to fill whatever limit it is given, so every value
draws the same curve, just later:

| Cap | Spiked at | When | `anon` throughout |
|---|---|---|---|
| 8G | 47% | 7 min into the ways pass | 0.25GB |
| 12G | 32% | 8 min in | 0.25GB |

The import's own memory never moved. There is nothing there to cap, so
`import-guarded` asks for no `MemoryMax` by default. What bounds it instead is
`MemorySwapMax=0` — runaway anonymous memory shows up as pressure at once
instead of disappearing into swap — and the watchdog. Pass `LIMIT=16G` to ask
for a cap anyway, keeping it well above the extract's node table.

**Turn `DEBUG` off.** With it on, Django keeps the last 9000 statements it ran,
fully interpolated, so that `connection.queries` can be inspected. One
`bulk_create` of a thousand streets leaves 0.6MB of SQL behind — measured — and
a full deque is 4.9GB. A Poland import grew from 0.02GB of anonymous memory to
3.58GB across its write phases, in step with the batches, and was stopped 45
minutes in. The long commands now replace the log with a zero-length deque and
say so, so ignoring this costs speed rather than the import, but it is still
the first thing to check.

Watch what else is holding memory, because the import is not competing with
itself. A Poland import that finished its heavy passes with a 1.99% pressure
peak was stopped at 50% two minutes in an hour later, on the same machine with
the same settings — the difference was `locations-local-postgis-1` sitting on
7.8GB of page cache from the first run, and OpenSearch on 3.8GB that an import
never touches. Before a large one:

```bash
docker compose -f docker-compose.local.yml stop opensearch   # unused until reindex
docker compose -f docker-compose.local.yml restart postgis    # drops its page cache
```

`import-guarded` prints the biggest holders at startup so this is visible
rather than deduced afterwards.

The watchdog is the guard that works: it stops the scope when the user
manager's own pressure stays above 30%, well below the 50% where oomd starts
choosing victims:

```bash
make import-guarded france              # logs to local/import-<timestamp>.log
THRESHOLD=20 make import-guarded france # stop earlier
LIMIT=16G make import-guarded france    # ask for a cap as well
```

It exits 75 if it had to stop, so a wrapper can retry with a smaller limit.
It also sweeps orphaned node-index files before and after: a killed process
runs no cleanup handler, and that file is the size of the extract's node
table — 5.3GB for France.

#### On a server

The pressure rule is not a desktop thing. Ubuntu ships it in the
`systemd-oomd` package, as a drop-in on `user@.service`:

```
ManagedOOMMemoryPressure=kill
ManagedOOMMemoryPressureLimit=50%
```

and `systemd-oomd.service` is `WantedBy=multi-user.target`, so a server
install has the same behaviour wherever that package is present. Check the
machine rather than assume:

```bash
systemctl is-active systemd-oomd          # is anything killing on pressure?
systemctl --user show-environment         # is there a user manager to kill in?
```

`import-guarded` picks its cgroup from what it finds, and says which it got:

| Situation | What it uses |
|---|---|
| root on a systemd host | a **system scope**, in `system.slice` — outside the user manager oomd watches, which is the best place for an unattended import |
| ordinary login or SSH session | a **user scope**, capped, with the watchdog |
| no systemd — container, CI | the command as it is, and a warning that there is no cap |

oomd has a second rule, on swap usage, which kills the heaviest swap user in
a slice. Ubuntu ships it as `ManagedOOMSwap=auto` on `-.slice` — off — so only
the pressure rule is live out of the box, and the watchdog reports swap in its
trace rather than acting on it. If you turned that rule on, watch swap too.

The watchdog stands down rather than refusing when there is nothing to
outrun: no `user@N.service` above the process, no readable `memory.pressure`
(cgroup v1, or PSI off), or no `systemd-oomd`. It finds the user manager by
walking up its own cgroup, so a uid other than 1000 is fine.

`make import` only wraps the command when `systemctl --user` answers, which
over SSH as an ordinary user it does and as root it usually does not — so as
root it runs uncapped. Prefer `sudo make import-guarded` there, which gets the
system scope. One more thing about SSH: a user scope belongs to the login
session, so `loginctl enable-linger <user>` is what keeps an import alive
after you log out.

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
make os-index                      # every search index: live, rollback or stale
make os-index remove               # what would be removed (KEEP=1 ISO=pl narrow it)
make os-index remove delete        # remove it
make os-index rollback ISO=pl      # which previous indexes would go back live
make os-index rollback apply ISO=pl  # switch to them
make os-index forward apply ISO=pl   # and back
make demo                          # the Leaflet page on :8000
make local-stop                    # stop the containers; local-up resumes them
make local-down                    # remove the containers, keeping the data
make local-reset                   # stop and DELETE the data
```

Individual steps are available too: `make import`, `make update`, `make merge`,
`make recalculate`, `make reindex`, `make os-index`, `make shell`,
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

The same applies to any index published from it, and there the obligation is
ours rather than yours: a published snapshot is a derivative database, offered
under ODbL, and carries

> © OpenStreetMap contributors

Coastlines come from [osmdata.openstreetmap.de](https://osmdata.openstreetmap.de),
which is ODbL for the same reason.

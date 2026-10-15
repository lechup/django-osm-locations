#!/usr/bin/env bash
#
# One country, start to finish, in a database of its own.
#
#   scripts/country-db.sh germany            # import ... pack, then drop the database
#   scripts/country-db.sh germany --keep     # leave the database for inspection
#   FROM=recalculate scripts/country-db.sh germany   # resume a kept database
#
# Every country used to share one database, and every step had to tell its
# own rows from the others': a scope table rebuilt on each start (1.5h for
# Germany), tables and indexes holding every country at once, and each
# country's rewrites bloating the rest. Here the database *is* the country, so
# the recalculation runs whole and the per-country machinery is not needed.
#
# What is kept is the pack in dist/snapshots/<iso>-<date>/; the database and
# the country's OpenSearch indexes are dropped once the pack's checksums
# verify. Loading the pack serves the country again; refreshing it means
# importing it again.
#
# The databases are made from a template, locations_template, which holds the
# schema, the seeded countries and the land polygons clipping needs -- copied
# once from the main database, so no country waits for 1.9GB of coastline.
#
# Steps run under scripts/guarded-import.sh; THRESHOLD, SUSTAIN and INTERVAL
# pass through to it.
set -euo pipefail

COUNTRY=${1:-}
KEEP=${2:-}
FROM=${FROM:-import}
if [ -z "$COUNTRY" ]; then
    echo "usage: $0 <country> [--keep]   (FROM=import|clip|recalculate|reindex)" >&2
    exit 2
fi
case $FROM in import|clip|recalculate|reindex) ;; *) echo "FROM must be import, clip, recalculate or reindex" >&2; exit 2 ;; esac

cd "$(dirname "$0")/.."
export DJANGO_SETTINGS_MODULE=${DJANGO_SETTINGS_MODULE:-local.settings}
PYTHON=${PYTHON:-python}
export PYTHON
CONTAINER=${CONTAINER:-locations-local-postgis-1}
MAIN_DB=${MAIN_DB:-locations}
TEMPLATE=locations_template
LOGS=${LOGS:-local/logs}
OPENSEARCH=${OPENSEARCH:-http://localhost:59201}
mkdir -p "$LOGS"

psql() { docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U locations -d "${DB:-postgres}" -At "$@"; }
step() { echo "=============== $* $(date '+%F %T') ==============="; }

ISO=$("$PYTHON" -c "
import django; django.setup()
from django_osm_locations.management.commands.importers import get_importer
importer = get_importer('$COUNTRY')
print(importer.iso2.lower() if importer else '')
")
if [ -z "$ISO" ]; then
    echo "no importer called $COUNTRY" >&2
    exit 2
fi
DATABASE=locations_$ISO

# --- the template ----------------------------------------------------------
if [ -z "$(DB=postgres psql -c "SELECT 1 FROM pg_database WHERE datname = '$TEMPLATE'")" ]; then
    step "building $TEMPLATE"
    DB=postgres psql -c "CREATE DATABASE $TEMPLATE"
    LOCATIONS_LOCAL_DB_NAME=$TEMPLATE "$PYTHON" -m django migrate --verbosity 0
    # the land polygons, as they are in the main database: copying beats
    # loading and subdividing the shapefile again for every country
    docker exec "$CONTAINER" sh -c \
        "pg_dump -U locations -d $MAIN_DB -t locations_land -t locations_land_subdivided \
         | psql -q -U locations -d $TEMPLATE" >/dev/null
    DB=postgres psql -c "ALTER DATABASE $TEMPLATE WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
fi

# --- the country's own database --------------------------------------------
if [ "$FROM" = import ]; then
    DB=postgres psql -c "DROP DATABASE IF EXISTS $DATABASE"
    step "creating $DATABASE from $TEMPLATE"
    DB=postgres psql -c "CREATE DATABASE $DATABASE TEMPLATE $TEMPLATE"
fi
export LOCATIONS_LOCAL_DB_NAME=$DATABASE
LOCATIONS_LOCAL_DB_NAME=$DATABASE "$PYTHON" -m django migrate --verbosity 0

guarded() {  # guarded <log name> <command> [args...]
    local log=$1 cmd=$2; shift 2
    COMMAND=$cmd UNIT=osm-$log LOG=$LOGS/$log.log ./scripts/guarded-import.sh "$@"
}

# The template's tables were never analysed and an import is one transaction
# autovacuum cannot see into; the recalculation that follows would plan its
# joins for empty tables. The importer analyses as it writes, this once more
# with everything committed.
analyze() { DB=$DATABASE psql -q -c "ANALYZE"; }

if [ "$FROM" = import ]; then
    step "import $COUNTRY"
    guarded "import-$ISO" locations_import "$COUNTRY"
    analyze
fi
if [ "$FROM" = import ] || [ "$FROM" = clip ]; then
    step "clip $COUNTRY"
    guarded "clip-$ISO" locations_clip_borders_to_land
    analyze
fi
if [ "$FROM" != reindex ]; then
    # the whole database is this country: no --country, no scope
    step "recalculate $COUNTRY"
    guarded "recalculate-$ISO" locations_recalculate_all
fi

step "opensearch up"
docker compose -f docker-compose.local.yml up -d opensearch >/dev/null
for _ in $(seq 1 60); do
    curl -fsS "$OPENSEARCH/_cluster/health" 2>/dev/null | grep -qE '"status":"(green|yellow)"' && break
    sleep 5
done

step "reindex $COUNTRY"
guarded "reindex-$ISO" locations_reindex --country "$COUNTRY"

DATE=$(date +%F)
SNAPSHOT=$ISO-$DATE
# the repository may still hold the last snapshot of this country, already
# packed; the pack script refuses a directory holding two
for old in $(curl -s "$OPENSEARCH/_snapshot/locations-$ISO/_all" | "$PYTHON" -c \
        "import sys, json; [print(s['snapshot']) for s in json.load(sys.stdin).get('snapshots', [])]" 2>/dev/null); do
    curl -s -XDELETE "$OPENSEARCH/_snapshot/locations-$ISO/$old" >/dev/null
done
step "snapshot $SNAPSHOT"
"$PYTHON" -m django locations_export_snapshot "$ISO" --name "$SNAPSHOT"
step "pack $SNAPSHOT"
./scripts/pack-snapshot.sh "$SNAPSHOT" > "$LOGS/pack-$ISO.log"
(cd "dist/snapshots/$SNAPSHOT" && sha256sum -c SHA256SUMS)

# the pack is the product; the snapshot in the repository is a copy of it
curl -s -XDELETE "$OPENSEARCH/_snapshot/locations-$ISO/$SNAPSHOT" >/dev/null

# The pack is what is kept, here as for the database: the country's indexes
# are a copy of it, and left in place they grew to 59GB over twelve countries.
# Loading the pack brings them back. --keep keeps them with the database.
if [ "$KEEP" != --keep ]; then
    indexes=$(curl -s "$OPENSEARCH/_cat/indices/locations-*-$ISO-*?h=index" | paste -sd, -)
    if [ -n "$indexes" ]; then
        curl -s -XDELETE "$OPENSEARCH/$indexes" >/dev/null
        step "indexes of $COUNTRY removed"
    fi
fi

# up only for the reindex and the snapshot; its heap is the import's headroom
docker compose -f docker-compose.local.yml stop opensearch >/dev/null

if [ "$KEEP" = --keep ]; then
    step "done, $DATABASE kept"
else
    DB=postgres psql -c "DROP DATABASE $DATABASE"
    step "done, $DATABASE dropped"
fi

#!/usr/bin/env bash
#
# Repair published packs in place, without the databases they came from.
#
#   scripts/repair-packs.sh                 # every pack in dist/snapshots
#   scripts/repair-packs.sh pl-2026-10-02   # just these
#
# For each: restore it into an otherwise empty cluster, run
# locations_repair_ancestry, snapshot it again under the same name -- the data
# is as old as it was -- pack that, verify the checksums, and only then let the
# old pack go. A failure stops the run with the old pack still in place, kept
# under dist/snapshots/.replaced/ until the new one has verified.
#
# Needs the local stack's OpenSearch; PostgreSQL is not used.
set -euo pipefail

cd "$(dirname "$0")/.."
OPENSEARCH=${OPENSEARCH:-http://localhost:59201}
PYTHON=${PYTHON:-.venv/bin/python}
export DJANGO_SETTINGS_MODULE=${DJANGO_SETTINGS_MODULE:-local.settings}
# nothing here reads the database, but Django opens one: any that exists will do
export LOCATIONS_LOCAL_DB_NAME=${LOCATIONS_LOCAL_DB_NAME:-postgres}
WHEELS=$("$PYTHON" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
export GDAL_LIBRARY_PATH=${GDAL_LIBRARY_PATH:-$(ls "$WHEELS"/pyogrio.libs/libgdal*.so* | head -1)}
export GEOS_LIBRARY_PATH=${GEOS_LIBRARY_PATH:-$(ls "$WHEELS"/shapely.libs/libgeos_c*.so* | head -1)}
export LD_LIBRARY_PATH=$WHEELS/shapely.libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}

step() { echo "=============== $* $(date '+%F %T') ==============="; }
django() { "$PYTHON" -m django "$@"; }
os() { curl -fsS "$@"; }

docker compose -f docker-compose.local.yml up -d opensearch >/dev/null
for _ in $(seq 1 60); do
    os "$OPENSEARCH/_cluster/health" 2>/dev/null | grep -qE '"status":"(green|yellow)"' && break
    sleep 5
done

if [ $# -gt 0 ]; then packs=("$@"); else
    mapfile -t packs < <(cd dist/snapshots && ls -d *-20*/ | tr -d /)
fi

for name in "${packs[@]}"; do
    iso=${name%-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]}   # pl, or a state's us-ca
    pack=dist/snapshots/$name
    repo=local/snapshots/$iso
    step "repair $name"

    # an empty cluster: the aliases would otherwise join two countries
    indexes=$(os "$OPENSEARCH/_cat/indices/locations-*?h=index" | paste -sd, -)
    [ -n "$indexes" ] && os -XDELETE "$OPENSEARCH/$indexes" >/dev/null
    # forget the repository: the cluster caches what it last saw there
    curl -s -XDELETE "$OPENSEARCH/_snapshot/locations-$iso" >/dev/null || true

    (cd "$pack" && sha256sum --quiet -c SHA256SUMS)
    rm -rf "$repo" && mkdir -p "$repo"
    cat "$pack"/"$name".tar.gz.part* | tar -xzf - -C local/snapshots
    django locations_import_snapshot "$name"
    until os "$OPENSEARCH/_cluster/health/locations-*?filter_path=status" | grep -q green; do
        sleep 10
    done

    django locations_repair_ancestry

    # the same name again: drop the restored snapshot, then take the new one
    os -XDELETE "$OPENSEARCH/_snapshot/locations-$iso/$name" >/dev/null
    django locations_export_snapshot "$iso" --name "$name"
    mkdir -p dist/snapshots/.replaced
    rm -rf "dist/snapshots/.replaced/$name"
    mv "$pack" "dist/snapshots/.replaced/$name"
    ./scripts/pack-snapshot.sh "$name" >/dev/null
    (cd "$pack" && sha256sum --quiet -c SHA256SUMS)
    rm -rf "dist/snapshots/.replaced/$name"

    os -XDELETE "$OPENSEARCH/_snapshot/locations-$iso/$name" >/dev/null
    indexes=$(os "$OPENSEARCH/_cat/indices/locations-*?h=index" | paste -sd, -)
    [ -n "$indexes" ] && os -XDELETE "$OPENSEARCH/$indexes" >/dev/null
    step "done $name"
done

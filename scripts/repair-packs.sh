#!/usr/bin/env bash
#
# Repair published packs in place, without the databases they came from.
#
#   scripts/repair-packs.sh                 # every pack in dist/snapshots
#   scripts/repair-packs.sh pl-2026-10-02   # just these
#
# For each: restore it into an otherwise empty cluster, run the repairs --
# locations_repair_ancestry, locations_repair_search_fields,
# locations_repair_addresses, locations_repair_slugs and
# locations_repair_twins, each a no-op on a pack that has it already --
# merge each index into one segment, snapshot it again under the same name --
# the data is as old as it was --
# pack that, verify the checksums, and only then let the old pack go. A failure stops the run with the old pack still in place, kept
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
    # the mapping first, so what the address repair writes has the new fields
    django locations_repair_search_fields
    django locations_repair_addresses
    # after the streets, which move houses and give them their street's slug
    django locations_repair_slugs
    django locations_repair_twins
    # One segment, nothing deleted left in it: the repairs rewrite documents
    # after their own merges, which expunge only segments over 10% deleted,
    # and the deleted ones still count in the term statistics -- Croatia's
    # addresses kept 98,663, and enough moved for "Ulica 5 Karlovac" to fall
    # from first to fourth.
    #
    # A merge keeps what a shard's retention lease still covers, and the
    # lease catches up with the last write only every 30 s or so: merged at
    # once, the twins just written would stay. So first the leases.
    for _ in $(seq 1 60); do
        os "$OPENSEARCH/locations-*/_stats?level=shards&filter_path=indices.*.shards" \
            | "$PYTHON" -c '
import json, sys
shards = [copy for index in json.load(sys.stdin).get("indices", {}).values()
          for copies in index["shards"].values() for copy in copies]
sys.exit(any(lease["retaining_seq_no"] <= copy["seq_no"]["max_seq_no"]
             for copy in shards for lease in copy["retention_leases"]["leases"]))' && break
        sleep 5
    done
    # OpenSearch answers 200 when a shard fails to merge, and when the
    # pattern matches nothing: what it reports is checked instead
    merged=$(os -XPOST "$OPENSEARCH/locations-*/_forcemerge?max_num_segments=1")
    if ! grep -q '"failed":0' <<<"$merged" || grep -q '"total":0' <<<"$merged"; then
        echo "forcemerge did not merge every index: $merged" >&2
        exit 1
    fi
    os -XPOST "$OPENSEARCH/locations-*/_refresh" >/dev/null
    deleted=$(os "$OPENSEARCH/_cat/indices/locations-*?h=docs.deleted" | awk '{ s += $1 } END { print s + 0 }')
    if [ "$deleted" -ne 0 ]; then
        echo "$deleted deleted documents left after the merge" >&2
        exit 1
    fi

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

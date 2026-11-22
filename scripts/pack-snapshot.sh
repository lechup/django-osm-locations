#!/usr/bin/env bash
#
# Turn a snapshot directory into release assets.
#
# A GitHub release asset may not exceed 2 GB and a country's index is larger
# than that, so what gets published is a tar split into parts, with the
# checksums that let a consumer know they reassembled it correctly. Nothing
# here talks to GitHub: it writes files and prints the release notes to go
# with them.
#
#   scripts/pack-snapshot.sh pl-2026-09-24
#
set -euo pipefail

SNAPSHOT=${1:-}
if [ -z "$SNAPSHOT" ]; then
    echo "usage: $0 <snapshot-name> [source-dir] [output-dir]" >&2
    exit 2
fi

# A published snapshot is named <country>-<date> and lives in a directory
# named for that country; that is the convention the export command follows.
# everything before the date: pl-2026-09-24 is in pl, a state's
# us-ca-2026-10-18 in us-ca
SOURCE=${2:-local/snapshots/${SNAPSHOT%-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]}}
OUTPUT=${3:-dist/snapshots/$SNAPSHOT}

# 1.9 GB, leaving room under GitHub's 2 GB limit for nothing in particular --
# the limit is on the asset, and a part that lands exactly on it is a bad bet.
PART_SIZE=${PART_SIZE:-1900M}

if [ ! -d "$SOURCE" ]; then
    echo "no such directory: $SOURCE" >&2
    echo "pass the snapshot directory as the second argument if it is elsewhere" >&2
    exit 1
fi

# The repository manifest lists every snapshot in the directory. Publishing a
# directory that holds two of them ships both, and a consumer who wanted one
# country gets a manifest announcing another whose files they did not ask for.
python3 - "$SOURCE" "$SNAPSHOT" <<'PY'
import glob, json, os, sys

source, wanted = sys.argv[1], sys.argv[2]
def generation(path):
    # index-10 is newer than index-3, and sorts before it as text
    suffix = os.path.basename(path).split('-', 1)[1]
    return int(suffix) if suffix.isdigit() else -1


manifests = [p for p in glob.glob(os.path.join(source, 'index-*')) if generation(p) >= 0]
if not manifests:
    sys.exit('{} is not a snapshot repository: no index-N manifest'.format(source))

latest = max(manifests, key=generation)
held = json.load(open(latest)).get('snapshots') or []
names = [s.get('name') for s in held]
if wanted not in names:
    sys.exit('{} holds {}, not {}'.format(source, ', '.join(names) or 'nothing', wanted))
if len(names) > 1:
    sys.exit(
        '{} holds {} snapshots: {}. Publishing it publishes all of them. '
        'Snapshot one country per directory.'.format(source, len(names), ', '.join(names)))
PY

PARENT=$(dirname "$SOURCE")
NAME=$(basename "$SOURCE")

rm -rf "$OUTPUT"
mkdir -p "$OUTPUT"

echo "packing $SOURCE"
tar -C "$PARENT" -cf - "$NAME" \
    | gzip -6 \
    | split -b "$PART_SIZE" -d -a 2 - "$OUTPUT/$SNAPSHOT.tar.gz.part"

( cd "$OUTPUT" && sha256sum "$SNAPSHOT".tar.gz.part* > SHA256SUMS )

# What built it, for the notes: the same code that exported it, as the pack's
# own metadata says (locations_import_snapshot --list shows that too).
BUILT=$("${PYTHON:-python3}" -c 'import django_osm_locations as p; print("Built with django-osm-locations {}; needs {} or later to load.".format(p.__version__, p.PACKS_LOAD_WITH))' 2>/dev/null || true)

echo
du -ch "$OUTPUT/$SNAPSHOT".tar.gz.part* | tail -1
echo "assets in $OUTPUT"
echo
echo "--- release notes ---"
cat <<NOTES
OpenSearch snapshot of the $NAME index, ready to restore. No OSM extract, no
import, no PostGIS.
${BUILT:+
$BUILT
}
Download every part, check them, and restore:

    sha256sum -c SHA256SUMS
    mkdir -p local/snapshots
    cat $SNAPSHOT.tar.gz.part* | tar -xzf - -C local/snapshots
    make load-snapshot $SNAPSHOT

Countries merge: load another one afterwards and both are searched together.

Derived from OpenStreetMap data and published under the Open Database
License. © OpenStreetMap contributors.
NOTES

#!/usr/bin/env bash
#
# Run a long import without risking the desktop session.
#
# systemd-oomd kills on memory pressure across the whole user manager, not on
# how much any one process is using, and it takes dbus and the editor with it.
# Two guards, because the cap alone is not enough: reclaim inside a capped
# scope still raises the pressure oomd watches.
#
#   1. the command runs in a scope of its own with MemoryMax, so an overrun is
#      the import's problem
#   2. this watches the user manager's own pressure and stops the scope first,
#      well below the 50% where oomd starts killing
#
#   scripts/guarded-import.sh france
#   LIMIT=6G THRESHOLD=25 scripts/guarded-import.sh poland denmark
#
set -uo pipefail

if [ "$#" -eq 0 ]; then
    echo "usage: $0 <country> [country...]" >&2
    exit 2
fi

LIMIT=${LIMIT:-8G}
# oomd fires at 50%; stop well before it, and only on a sustained reading --
# a single spike is what page cache filling looks like and is harmless.
THRESHOLD=${THRESHOLD:-30}
SUSTAIN=${SUSTAIN:-6}
INTERVAL=${INTERVAL:-10}
UNIT=${UNIT:-osm-import}
PYTHON=${PYTHON:-python}
# Where the importer puts the node index; must match LOCATIONS_CACHE_DIR.
CACHE_DIR=${CACHE_DIR:-local}
LOG=${LOG:-local/import-$(date +%Y%m%d-%H%M%S).log}
TRACE=${TRACE:-${LOG%.log}.pressure}

PRESSURE=/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/memory.pressure
if [ ! -r "$PRESSURE" ]; then
    echo "cannot read $PRESSURE -- refusing to run unguarded" >&2
    exit 1
fi

mkdir -p "$(dirname "$LOG")"

# A killed process runs no cleanup handler, so the node index -- tens of
# gigabytes for a large country -- outlives it. Sweep before and after.
sweep_index() {
    local found
    found=$(find "$CACHE_DIR" -maxdepth 1 -name 'osm-node-locations-*.idx' 2>/dev/null)
    if [ -n "$found" ]; then
        echo "$found" | while read -r stale; do
            echo "removing orphaned node index $stale ($(du -h "$stale" | cut -f1))"
            rm -f "$stale"
        done
    fi
}
trap sweep_index EXIT
sweep_index

# -u so a kill does not discard the progress written so far: without it the
# last crash left a zero-byte log and nothing to read.
systemd-run --user --scope -p MemoryMax="$LIMIT" -p MemorySwapMax=0 \
    --unit="$UNIT" --quiet -- \
    "$PYTHON" -u -m django locations_import "$@" > "$LOG" 2>&1 &
RUNNER=$!

echo "import: $* (limit $LIMIT, stop above ${THRESHOLD}% for $((SUSTAIN * INTERVAL))s)"
echo "log:    $LOG"
echo "trace:  $TRACE"

scope_dir() {
    local d=/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice/$UNIT.scope
    [ -d "$d" ] && echo "$d"
}

over=0
while kill -0 "$RUNNER" 2>/dev/null; do
    avg10=$(awk '/^some/ {sub(/avg10=/, "", $2); print $2; exit}' "$PRESSURE")
    used=0
    dir=$(scope_dir)
    if [ -n "$dir" ] && [ -r "$dir/memory.current" ]; then
        used=$(awk '{printf "%.2f", $1/1073741824}' "$dir/memory.current")
    fi
    printf '%s pressure=%s%% scope=%sGB\n' "$(date +%H:%M:%S)" "$avg10" "$used" >> "$TRACE"

    if awk -v a="$avg10" -v t="$THRESHOLD" 'BEGIN {exit !(a > t)}'; then
        over=$((over + 1))
        if [ "$over" -ge "$SUSTAIN" ]; then
            echo "pressure $avg10% > $THRESHOLD% for $((SUSTAIN * INTERVAL))s -- stopping the import" \
                | tee -a "$TRACE"
            systemctl --user stop "$UNIT.scope" 2>/dev/null
            wait "$RUNNER" 2>/dev/null
            exit 75   # EX_TEMPFAIL: try again with a smaller limit
        fi
    else
        over=0
    fi
    sleep "$INTERVAL"
done

wait "$RUNNER"
STATUS=$?
echo "import finished with status $STATUS; peak pressure $(awk '{gsub(/pressure=|%/,"",$2); if ($2+0>m) m=$2+0} END {print m"%"}' "$TRACE")"
exit "$STATUS"

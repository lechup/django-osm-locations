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

# No memory cap by default, which is not laziness -- it is what two measured
# runs say. memory.max counts page cache, the node index is written through
# mmap so its pages are dirty, and a cgroup at its limit has to write them back
# inside reclaim: scattered synchronous IO, which stalls, which is pressure.
# Page cache expands to whatever limit it is given, so every value produces the
# same curve, just later. At 8G a France import spiked at 47% seven minutes in;
# at 12G it spiked at 32% eight minutes in, with anon steady at 0.25GB both
# times. There is nothing there to cap.
#
# What bounds the import instead: MemorySwapMax=0, so runaway anonymous memory
# shows up as pressure immediately rather than disappearing into swap, and the
# watchdog below, which stops it at 25% while oomd fires at 50%.
#
# Set LIMIT to ask for one anyway; keep it well above the extract's node table,
# 16 bytes a node.
LIMIT=${LIMIT:-}
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

# Find the user manager's own cgroup rather than assuming uid 1000 and a
# desktop layout: read it off this process and walk up to the user@N.service
# component. Everything below that is what oomd's pressure rule considers.
manager_cgroup() {
    local path
    path=$(cut -d: -f3 /proc/self/cgroup | head -1)
    while [ -n "$path" ] && [ "$path" != "/" ]; do
        case "${path##*/}" in
            user@*.service) echo "/sys/fs/cgroup$path"; return 0 ;;
        esac
        path=${path%/*}
    done
    return 1
}

# Three reasons there may be nothing to watch, and none of them is a reason to
# refuse the import:
#
#   * no user@N.service above us -- a system service, a container, a server
#     where root has no user manager: oomd's user-pressure rule cannot reach us
#   * no memory.pressure -- cgroup v1, or PSI off in the kernel: oomd needs PSI
#     to act on pressure, so it cannot fire this way either
#   * systemd-oomd not installed or not running -- Ubuntu ships it in its own
#     package, WantedBy=multi-user.target, so a server has it only if installed
#
# In each case the cap still applies and the watchdog simply has nothing to do.
MANAGER=$(manager_cgroup || true)
PRESSURE=""
if [ -n "$MANAGER" ] && [ -r "$MANAGER/memory.pressure" ]; then
    PRESSURE="$MANAGER/memory.pressure"
fi

OOMD=no
if systemctl is-active systemd-oomd.service >/dev/null 2>&1; then
    OOMD=yes
fi

# oomd has a second rule, on swap usage, that kills the heaviest swap user in
# the slice. Ubuntu ships it as ManagedOOMSwap=auto on -.slice, which is off,
# so this only reports swap rather than acting on it -- a threshold here would
# stop imports to prevent something that is not enabled. Check the host if you
# have changed it: systemctl show -p ManagedOOMSwap -- -.slice
swap_percent() {
    awk '/^SwapTotal:/ {t=$2} /^SwapFree:/ {f=$2}
         END {if (t > 0) printf "%.0f", 100*(t-f)/t; else print 0}' /proc/meminfo
}


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

# Where to put the scope. A system scope lands in system.slice, outside the
# user manager oomd watches for pressure, which is what you want on a server
# and needs root; a user scope is what a desktop login has. With no systemd at
# all -- a container, a CI runner -- run the command as it is and say so,
# because a missing cap is worth knowing about.
LIMITS=(-p MemorySwapMax=0)
[ -n "$LIMIT" ] && LIMITS+=(-p MemoryMax="$LIMIT")

SCOPE=()
MODE=plain
if [ "$(id -u)" = 0 ] && systemctl is-system-running >/dev/null 2>&1; then
    SCOPE=(systemd-run --scope "${LIMITS[@]}" --unit="$UNIT" --quiet --)
    MODE="system scope"
elif systemctl --user show-environment >/dev/null 2>&1; then
    SCOPE=(systemd-run --user --scope "${LIMITS[@]}" --unit="$UNIT" --quiet --)
    MODE="user scope"
fi

# Say which guards are actually in force, in one place, because "cap only" and
# "no cap" in the same breath is how you end up believing the wrong one.
if [ "$MODE" = plain ]; then
    echo "warning: no systemd scope here, so swap is not held off this import" >&2
    echo "         and there is nothing to stop but the process itself." >&2
elif [ -z "$PRESSURE" ] && [ "$OOMD" = yes ]; then
    echo "warning: systemd-oomd is running but its trigger is not readable" >&2
    echo "         ($([ -n "$MANAGER" ] && echo "no memory.pressure under $MANAGER" \
        || echo 'no user manager above this process')). Cap only." >&2
fi
if [ -z "$PRESSURE" ] && [ "$OOMD" = no ]; then
    echo "systemd-oomd is not running here, so there is no pressure kill to"
    echo "outrun; the watchdog stands down."
fi

# -u so a kill does not discard the progress written so far: without it the
# last crash left a zero-byte log and nothing to read.
${SCOPE[@]+"${SCOPE[@]}"} "$PYTHON" -u -m django locations_import "$@" > "$LOG" 2>&1 &
RUNNER=$!

CAP=${LIMIT:+, limit $LIMIT}
CAP=${CAP:-, no cap}
if [ -n "$PRESSURE" ]; then
    echo "import: $* ($MODE$CAP, stop above ${THRESHOLD}% for $((SUSTAIN * INTERVAL))s)"
else
    echo "import: $* ($MODE$CAP)"
fi
echo "log:    $LOG"
echo "trace:  $TRACE"

# What else is holding memory, before the import needs it. This is not
# decoration: a Poland import that had sailed through with a 1.99% peak was
# stopped at 50% two minutes in an hour later, and the difference was a
# database container sitting on 7.8GB of page cache from the first run and a
# search engine on 3.8GB that an import never touches. Cheaper to print than
# to reason about afterwards.
echo "headroom:"
free -h | awk '/^(Mem|Pamięć)/ {printf "  %s available of %s, %s in cache\n", $NF, $2, $6}'
for cg in /sys/fs/cgroup/system.slice/docker-*.scope \
          /sys/fs/cgroup/user.slice/user-*.slice/user@*.service/app.slice/*/; do
    [ -r "$cg/memory.current" ] || continue
    awk -v n="$(basename "$cg")" '$1 > 2147483648 {printf "  %5.1f GB  %s\n", $1/1073741824, n}' \
        "$cg/memory.current"
done | sort -rn | head -5

# Ask systemd where it put the scope; the layout differs between versions and
# between a desktop and a server, and guessing it once already cost a session.
# systemctl, spelled the way the scope was started -- `--user` for a user
# scope, plain for a system one, nothing to ask when there is no scope.
sctl() {
    case "$MODE" in
        "user scope")   systemctl --user "$@" ;;
        "system scope") systemctl "$@" ;;
        *)              return 1 ;;
    esac
}

# Ask systemd where it put the scope; the layout differs between versions and
# between a desktop and a server, and guessing it once already cost a session.
scope_dir() {
    local path
    path=$(sctl show -p ControlGroup --value "$UNIT.scope" 2>/dev/null)
    [ -n "$path" ] && [ -d "/sys/fs/cgroup$path" ] && echo "/sys/fs/cgroup$path"
}

over=0
while kill -0 "$RUNNER" 2>/dev/null; do
    avg10=""
    [ -n "$PRESSURE" ] && avg10=$(awk '/^some/ {sub(/avg10=/, "", $2); print $2; exit}' "$PRESSURE")
    used=0
    anon=0
    cache=0
    dir=$(scope_dir)
    if [ -n "$dir" ] && [ -r "$dir/memory.current" ]; then
        used=$(awk '{printf "%.2f", $1/1073741824}' "$dir/memory.current")
        # anon is the import's own memory and should stay small; the rest is
        # page cache for the index and the extract, and is reclaimable. Which
        # of the two is filling the cgroup decides whether a spike means a
        # regression or a cap set too low.
        read -r anon cache < <(awk '/^anon /{a=$2} /^file /{f=$2}
            END {printf "%.2f %.2f", a/1073741824, f/1073741824}' "$dir/memory.stat")
    fi
    printf '%s pressure=%s%% scope=%sGB (anon=%sGB cache=%sGB) swap=%s%%\n' \
        "$(date +%H:%M:%S)" "${avg10:-n/a}" "$used" "$anon" "$cache" \
        "$(swap_percent)" >> "$TRACE"

    if [ -n "$avg10" ] && awk -v a="$avg10" -v t="$THRESHOLD" 'BEGIN {exit !(a > t)}'; then
        over=$((over + 1))
        if [ "$over" -ge "$SUSTAIN" ]; then
            echo "pressure $avg10% > $THRESHOLD% for $((SUSTAIN * INTERVAL))s -- stopping the import" \
                | tee -a "$TRACE"
            sctl stop "$UNIT.scope" 2>/dev/null || kill "$RUNNER" 2>/dev/null
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
if [ -n "$PRESSURE" ]; then
    echo "import finished with status $STATUS; peak pressure $(awk \
        '{gsub(/pressure=|%/,"",$2); if ($2+0>m) m=$2+0} END {print m"%"}' "$TRACE")"
else
    echo "import finished with status $STATUS"
fi
exit "$STATUS"

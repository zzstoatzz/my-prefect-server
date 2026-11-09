#!/bin/sh
set -eu
umask 077

volume=/System/Volumes/Data
state_dir=${MAC_DISK_WATCH_STATE_DIR:-"$HOME/Library/Application Support/mac-disk-watch"}
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
last=$state_dir/last
pending=$state_dir/pending.json

deliver() {
    if ! just --working-directory "$repo" --justfile "$repo/justfile" prefect \
        --no-prompt api POST /events --data "$(cat "$pending")" >/dev/null 2>&1; then
        printf '%s\n' 'mac-disk-watch: Prefect delivery failed; will retry the saved event.' >&2
        return 1
    fi
    jq -er '.[0].payload.state' "$pending" > "$last.tmp"
    mv "$last.tmp" "$last"
    rm "$pending"
}

if [ "${1:-}" != "--check" ]; then
    mkdir -p "$state_dir"
    if [ -f "$pending" ]; then
        deliver
    fi
fi

previous=healthy:writable
if [ -f "$last" ]; then
    previous=$(cat "$last")
fi
available=$(/bin/df -kP "$volume" | /usr/bin/awk 'NR == 2 { print $4 }')
case "$available" in
    ''|*[!0-9]*) printf '%s\n' 'mac-disk-watch: could not measure free space.' >&2; exit 1 ;;
esac
bytes=$((available * 1024))
index=$(/usr/bin/mdutil -s "$volume" 2>/dev/null || true)
case "$index" in
    *'Index is read-only.'*) spotlight=read-only ;;
    *'Indexing disabled.'*) spotlight=disabled ;;
    *'Indexing enabled.'*) spotlight=writable ;;
    *) spotlight=unknown ;;
esac

level=healthy
if [ "$spotlight" = read-only ] || [ "$spotlight" = disabled ] ||
    [ "$bytes" -lt 45000000000 ] ||
    { [ "${previous%%:*}" = critical ] && [ "$bytes" -lt 50000000000 ]; }; then
    level=critical
elif [ "$bytes" -lt 75000000000 ] ||
    { [ "${previous%%:*}" != healthy ] && [ "$bytes" -lt 85000000000 ]; }; then
    level=warning
fi

observed=$(/bin/date '+%Y-%m-%d %H:%M:%S %Z')
summary=$(printf '%s GB free on the Data volume. Spotlight index: %s. Checked %s.' \
    "$((bytes / 1000000000))" "$spotlight" "$observed")
if [ "${1:-}" = "--check" ]; then
    printf '%s — %s\n' "$level" "$summary"
    exit 0
fi

state="$level:$spotlight"
if [ "$state" = "$previous" ]; then
    exit 0
fi
jq -nc --arg id "$(/usr/bin/uuidgen | tr '[:upper:]' '[:lower:]')" \
    --arg occurred "$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --arg state "$state" --arg level "$level" --arg summary "$summary" \
    --arg spotlight "$spotlight" --argjson bytes "$bytes" \
    '[{id: $id, occurred: $occurred, event: "mac.storage.changed",
       resource: {"prefect.resource.id": "host.nate-mac", "prefect.resource.name": "Nate’s Mac"},
       related: [], payload: {state: $state, level: $level, summary: $summary,
                              spotlight: $spotlight, available_bytes: $bytes}}]' > "$pending.tmp"
mv "$pending.tmp" "$pending"
deliver

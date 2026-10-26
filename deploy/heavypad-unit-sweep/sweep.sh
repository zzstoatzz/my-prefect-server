#!/usr/bin/env bash
# Clear failed one-off (transient) user units once they are older than
# MAX_AGE_DAYS. `systemd-run --user` defaults to CollectMode=inactive, so a
# one-off that exits non-zero stays in `systemctl --user --failed` forever and
# buries new failures. Persistent units are never touched, and recent failures
# stay visible long enough to investigate. The journal keeps every log line.
set -euo pipefail

max_age_days="${MAX_AGE_DAYS:-3}"
cutoff=$(( $(date +%s) - max_age_days * 86400 ))

systemctl --user list-units --state=failed --no-legend --plain | awk '{print $1}' |
while read -r unit; do
  [ "$(systemctl --user show -p Transient --value "$unit")" = yes ] || continue
  failed_at="$(systemctl --user show -p InactiveEnterTimestamp --value "$unit")"
  [ -n "$failed_at" ] || continue
  if [ "$(date -d "$failed_at" +%s)" -lt "$cutoff" ]; then
    systemctl --user reset-failed "$unit"
    echo "cleared $unit (failed $failed_at)"
  fi
done

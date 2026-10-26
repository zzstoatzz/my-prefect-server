#!/usr/bin/env bash
# Install the failed one-off sweep on heavypad as a daily systemd user timer.
# No root needed: the stoat user has lingering enabled.
# Run on heavypad:  bash install.sh
set -euo pipefail

DEST="$HOME/.local/share/heavypad-unit-sweep"
UNIT_DIR="$HOME/.config/systemd/user"

mkdir -p "$DEST" "$UNIT_DIR"
install -m 0755 "$(dirname "$0")/sweep.sh" "$DEST/sweep.sh"

cat > "$UNIT_DIR/heavypad-unit-sweep.service" <<UNIT
[Unit]
Description=Clear failed one-off user units older than three days

[Service]
Type=oneshot
ExecStart=${DEST}/sweep.sh
UNIT

cat > "$UNIT_DIR/heavypad-unit-sweep.timer" <<UNIT
[Unit]
Description=Daily sweep of stale failed one-off user units

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now heavypad-unit-sweep.timer
systemctl --user start heavypad-unit-sweep.service
journalctl --user -u heavypad-unit-sweep.service -n 40 --no-pager -o cat
echo "==> failed user units now: $(systemctl --user list-units --state=failed --no-legend | wc -l)"

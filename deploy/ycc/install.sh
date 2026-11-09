#!/usr/bin/env bash
# Install the ycc daemon on heavypad as a systemd user service. No root
# needed: the stoat user has lingering enabled, so it survives reboots.
# Run on heavypad from a checkout, after build.sh:  bash deploy/ycc/install.sh
#
# The daemon runs in deploy/ycc/sandbox.sh and listens on loopback only;
# `tailscale serve --bg --https=12443 http://127.0.0.1:4327` publishes it on
# the tailnet. See README.md.
set -euo pipefail

SANDBOX="${YCC_SANDBOX:-$HOME/ycc}"
PORT="${YCC_PORT:-4327}"
DEST="$HOME/.local/share/ycc"
UNIT_DIR="$HOME/.config/systemd/user"
CONFIG="$SANDBOX/home/.config/ycc/ycc.toml"
HERE="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$DEST" "$UNIT_DIR" "$SANDBOX/home/.config/ycc" "$SANDBOX/work"
chmod 700 "$SANDBOX/home" "$SANDBOX/home/.config/ycc"
install -m 0755 "$HERE/sandbox.sh" "$DEST/sandbox.sh"
# The daemon writes settings changes back to this file, so only seed it.
[ -e "$CONFIG" ] || install -m 0600 "$HERE/ycc.toml" "$CONFIG"
chmod 600 "$CONFIG"

cat > "$UNIT_DIR/ycc.service" <<UNIT
[Unit]
Description=ycc daemon and web client, loopback behind tailscale serve
After=network-online.target

[Service]
Environment=YCC_SANDBOX=${SANDBOX}
ExecStart=${DEST}/sandbox.sh ycc daemon --web --addr 127.0.0.1:${PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable ycc
systemctl --user restart ycc

curl -sf -o /dev/null --max-time 3 --retry 30 --retry-delay 1 --retry-connrefused "http://127.0.0.1:${PORT}/" \
  || { echo "ycc did not answer on 127.0.0.1:${PORT}" >&2; systemctl --user status ycc --no-pager >&2 || true; exit 1; }
systemctl --user show ycc -p ActiveState -p NRestarts

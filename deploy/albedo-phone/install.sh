#!/usr/bin/env bash
# Install `albedo web` for the phone on heavypad as a systemd user service.
# No root needed: the stoat user has lingering enabled, so it survives reboots.
# Run on heavypad from a checkout:  bash deploy/albedo-phone/install.sh
#
# It runs in the same bwrap sandbox as the drift audit
# (deploy/albedo-sandbox/sandbox.sh) but in its own sandbox dir, so the
# audit's `albedo daemon --stop` never reaches this daemon. It listens on
# loopback only; `tailscale serve --bg --https=11443 http://127.0.0.1:4317`
# publishes it on the tailnet as PUBLIC_URL, which needs the operator set
# once (`sudo tailscale set --operator=stoat`). See README.md.
set -euo pipefail

SANDBOX="${ALBEDO_SANDBOX:-$HOME/albedo-phone}"
PUBLIC_URL="${ALBEDO_PHONE_URL:-https://heavypad.tailb660b6.ts.net:11443}"
PORT="${ALBEDO_PHONE_PORT:-4317}"
DEST="$HOME/.local/share/albedo-phone"
UNIT_DIR="$HOME/.config/systemd/user"
HERE="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$DEST" "$UNIT_DIR" "$SANDBOX/home/.albedo" "$SANDBOX/work"
chmod 700 "$SANDBOX/home" "$SANDBOX/home/.albedo"
install -m 0755 "$HERE/../albedo-sandbox/sandbox.sh" "$DEST/sandbox.sh"

cat > "$UNIT_DIR/albedo-phone.service" <<UNIT
[Unit]
Description=albedo web client for the phone, loopback behind tailscale serve
After=network-online.target

[Service]
Environment=ALBEDO_SANDBOX=${SANDBOX}
ExecStart=${DEST}/sandbox.sh albedo web --foreground --no-open --local --port ${PORT} --public-url ${PUBLIC_URL}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now albedo-phone
systemctl --user restart albedo-phone

for _ in $(seq 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:${PORT}/" || true)
  [ "$code" = 200 ] && break
  sleep 1
done
[ "$code" = 200 ] || { echo "albedo web did not answer on 127.0.0.1:${PORT} (last: ${code})" >&2; exit 1; }
systemctl --user show albedo-phone -p ActiveState -p NRestarts

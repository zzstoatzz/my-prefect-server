#!/usr/bin/env bash
# Install the heavypad Prefect home worker as a systemd service.
# Run as root:  sudo bash install.sh
# Requires /home/stoat/.config/prod-worker/env (PREFECT_API_URL + PREFECT_API_AUTH_STRING, 0600).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> stopping interim tmux worker (if any)"
sudo -u stoat tmux kill-session -t prefect 2>/dev/null || true
pkill -9 -f "worker start --pool home-pool" 2>/dev/null || true

echo "==> installing unit"
install -m 644 "$HERE/prefect-home-worker.service" /etc/systemd/system/prefect-home-worker.service
systemctl daemon-reload
systemctl enable --now prefect-home-worker.service

sleep 4
echo "==> status"
systemctl status prefect-home-worker.service --no-pager | head -15
echo
echo "done. tail logs with:  journalctl -u prefect-home-worker -f"

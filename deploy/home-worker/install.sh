#!/usr/bin/env bash
# Install the heavypad Prefect home worker as a systemd service.
# Run as root:  sudo bash install.sh
# Requires /home/stoat/.config/prod-worker/env (PREFECT_API_URL + PREFECT_API_AUTH_STRING, 0600).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
GUARD_DIR="$(cd "$HERE/../../tools/prefect-worker-guard" && pwd)"
ZIG_BIN="$(command -v zig || true)"
if [[ -z "$ZIG_BIN" && -x /home/stoat/.local/bin/zig ]]; then
  ZIG_BIN=/home/stoat/.local/bin/zig
fi
if [[ -z "$ZIG_BIN" ]]; then
  echo "zig not found; install it with zigup for user stoat first" >&2
  exit 1
fi

echo "==> stopping interim tmux worker (if any)"
sudo -u stoat tmux kill-session -t prefect 2>/dev/null || true
pkill -9 -f "worker start --pool home-pool" 2>/dev/null || true

echo "==> removing old shell watchdog (if present)"
systemctl disable --now prefect-home-worker-watchdog.timer 2>/dev/null || true
rm -f \
  /etc/systemd/system/prefect-home-worker-watchdog.timer \
  /etc/systemd/system/prefect-home-worker-watchdog.service \
  /usr/local/sbin/prefect-home-worker-watchdog

echo "==> building prefect-worker-guard"
"$ZIG_BIN" build --build-file "$GUARD_DIR/build.zig" -Doptimize=ReleaseSafe -p /usr/local

echo "==> installing guard config and unit"
install -m 644 "$HERE/prefect-worker-guard.env" /etc/prefect-worker-guard.env
install -m 644 "$HERE/prefect-home-worker.service" /etc/systemd/system/prefect-home-worker.service
systemctl daemon-reload
systemctl restart prefect-home-worker.service
systemctl enable prefect-home-worker.service

sleep 4
echo "==> status"
systemctl status prefect-home-worker.service --no-pager | head -15
echo
echo "done. tail logs with:  journalctl -u prefect-home-worker -f"

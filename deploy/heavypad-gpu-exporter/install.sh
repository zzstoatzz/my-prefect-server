#!/usr/bin/env bash
# Install the GPU exporter on heavypad as a systemd user service. No root needed:
# the stoat user has lingering enabled, so user services run without a login.
# Run on heavypad:  bash install.sh
#
# Prometheus scrapes it over the tailnet beside node_exporter — see the
# `heavypad-gpu` job in deploy/monitoring-values.yaml. Like node_exporter it
# binds ONLY to the tailnet address.
set -euo pipefail

TAILNET_IP="${TAILNET_IP:-100.96.216.23}"
DEST="$HOME/.local/share/heavypad-gpu-exporter"
UNIT_DIR="$HOME/.config/systemd/user"

mkdir -p "$DEST" "$UNIT_DIR"
install -m 0644 "$(dirname "$0")/gpu_exporter.py" "$DEST/gpu_exporter.py"

cat > "$UNIT_DIR/heavypad-gpu-exporter.service" <<EOF
[Unit]
Description=nvidia-smi metrics for Prometheus on the tailnet
After=network-online.target

[Service]
Environment=GPU_EXPORTER_LISTEN=${TAILNET_IP}:9835
ExecStart=/usr/bin/python3 -I ${DEST}/gpu_exporter.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now heavypad-gpu-exporter
systemctl --user restart heavypad-gpu-exporter

for _ in $(seq 10); do
  curl -sf --max-time 3 "http://${TAILNET_IP}:9835/metrics" | grep -q '^nvidia_smi_up 1' && break
  sleep 1
done
curl -sf --max-time 3 "http://${TAILNET_IP}:9835/metrics" | grep -q '^nvidia_smi_up 1' \
  || { echo "exporter not serving on ${TAILNET_IP}:9835" >&2; exit 1; }
if curl -s -o /dev/null --max-time 3 http://127.0.0.1:9835/metrics; then
  echo "WARNING: reachable on localhost — expected tailnet-only bind" >&2
  exit 1
fi
echo "==> ok: listening on ${TAILNET_IP}:9835, not on localhost"

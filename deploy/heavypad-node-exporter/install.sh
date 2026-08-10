#!/usr/bin/env bash
# Install node_exporter on heavypad so the in-cluster Prometheus can scrape the
# home box. Run as root:  sudo bash install.sh
#
# heavypad is not in the cluster, so it cannot be discovered by a ServiceMonitor.
# Prometheus scrapes it directly over the tailnet instead — see the
# `additionalScrapeConfigs` block in deploy/monitoring-values.yaml, and the
# `heavypad / host` dashboard in deploy/dashboards/.
#
# The exporter binds ONLY to the tailnet address. It is deliberately not
# reachable from localhost, the LAN, or the public internet.
set -euo pipefail

TAILNET_IP="${TAILNET_IP:-100.96.216.23}"

echo "==> installing prometheus-node-exporter"
DEBIAN_FRONTEND=noninteractive apt-get install -y -q prometheus-node-exporter

echo "==> binding to the tailnet address only ($TAILNET_IP:9100)"
sed -i "s|^ARGS=.*|ARGS=\"--web.listen-address=${TAILNET_IP}:9100\"|" \
  /etc/default/prometheus-node-exporter

# The tailnet address does not exist until tailscaled has brought up tailscale0.
# Without this ordering a reboot can start the exporter first, the bind fails,
# and the target silently stays down until someone notices the gap.
echo "==> ordering after tailscaled, with retry"
mkdir -p /etc/systemd/system/prometheus-node-exporter.service.d
cat > /etc/systemd/system/prometheus-node-exporter.service.d/10-tailnet.conf <<EOF
[Unit]
After=tailscaled.service network-online.target
Wants=tailscaled.service

[Service]
Restart=on-failure
RestartSec=5
EOF

systemctl daemon-reload
systemctl enable --now prometheus-node-exporter
systemctl restart prometheus-node-exporter

echo "==> verifying"
sleep 2
ss -lntp | grep -w 9100 || { echo "not listening on 9100" >&2; exit 1; }
if curl -s -o /dev/null --max-time 5 http://127.0.0.1:9100/metrics; then
  echo "WARNING: reachable on localhost — expected tailnet-only bind" >&2
  exit 1
fi
echo "==> ok: listening on ${TAILNET_IP}:9100, not on localhost"

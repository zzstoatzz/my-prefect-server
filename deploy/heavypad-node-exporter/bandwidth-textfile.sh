#!/usr/bin/env bash
# Run as root. Only numeric telemetry is readable; watchdog state stays private.
set -euo pipefail
install -d -o stoat -g stoat -m 0755 /var/lib/prometheus/bandwidth-watchdog
ln -sfn /var/lib/prometheus/bandwidth-watchdog/bandwidth.prom /var/lib/prometheus/node-exporter/bandwidth-watchdog.prom

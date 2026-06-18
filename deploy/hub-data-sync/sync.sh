#!/usr/bin/env bash
# Push the hub's data files from heavypad (home box, where the flows write) to
# the EU edge node over the tailnet, so the in-cluster hub serves FRESH data
# while staying EU-local-fast.
#
# Why this exists: we briefly repointed hub.waow.tech directly at a hub
# container on heavypad over the tailnet. That made every page load travel
# US-user -> EU-edge -> US-home and stream a ~400KB page back over a
# DERP-relayed residential link (~12s). The data is tiny (~4MB), so the right
# split is: serve the page at the edge, sync the data to it. Render is
# EU-local (~12ms); only ~4MB crosses the tailnet every few minutes, off the
# request path.
#
# Deployed on heavypad as a cron entry (every 3 min):
#   */3 * * * * /home/stoat/hub-data-sync.sh >> /home/stoat/hub-data-sync.log 2>&1
# Auth: a dedicated key ~/.ssh/hub_sync_ed25519, whose pubkey is in the EU
# node's root authorized_keys, restricted from="<heavypad-tailnet-ip>".
set -euo pipefail

EDGE="${HUB_EDGE_HOST:-root@100.78.52.120}"        # EU node, tailnet IP
SRC="${PREFECT_ANALYTICS_DIR:-/home/stoat/prefect-analytics}"
DEST="${HUB_EDGE_ANALYTICS_DIR:-/var/lib/prefect-analytics}"
KEY="${HUB_SYNC_KEY:-/home/stoat/.ssh/hub_sync_ed25519}"

rsync -az -e "ssh -i $KEY -o StrictHostKeyChecking=accept-new -o BatchMode=yes" \
  "$SRC/hub.duckdb" \
  "$SRC/llm-spend.jsonl" \
  "$EDGE:$DEST/"

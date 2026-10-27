#!/usr/bin/env bash
set -euo pipefail
export DOCKER_HOST=unix:///var/run/docker.sock
directory="$HOME/hcloud-builder/state/billing"
mkdir -p "$directory"
exec docker run --rm --user "$(id -u):$(id -g)" \
  --env-file "$HOME/.config/prefect-home/r2.env" -v "$directory:/data:ro" \
  rclone/rclone@sha256:2687085f718d3c628f7fdfb77c52a1d344332aed543110bb88088f2c70d43eb5 \
  copy /data r2:heavypad-prefect-backups/billing/hetzner-builders \
  --immutable --checksum --include='/*.json' --s3-no-check-bucket

#!/usr/bin/env bash
set -euo pipefail
export DOCKER_HOST=unix:///var/run/docker.sock
export PATH="$HOME/.local/bin:$PATH"
state="$HOME/.local/share/prefect-home"
mkdir -p "$state/storage"
if k3d cluster list --no-headers | awk '{print $1}' | grep -qx prefect-home; then
  echo 'prefect-home cluster already exists'
  exit 0
fi
k3d cluster create prefect-home \
  --servers 1 --agents 0 --image rancher/k3s:v1.35.5-k3s1 \
  --api-port 127.0.0.1:16443 --no-lb \
  --port '127.0.0.1:14200:30420@server:0' \
  --servers-memory 6g \
  --volume "$state/storage:/var/lib/rancher/k3s/storage@server:0" \
  --k3s-arg '--disable=traefik@server:0' \
  --k3s-arg '--disable=servicelb@server:0' \
  --kubeconfig-update-default=false --kubeconfig-switch-context=false

#!/usr/bin/env bash
set -euo pipefail
revision=$(git rev-parse --verify "${1:?pass the committed revision}^{commit}")
ssh_target=$(uv run python -c 'import sys; sys.path.insert(0, "scripts"); from prefect_target import target_config; print(target_config()["ssh_host"])')
release="prefect-edge/releases/$revision"
ssh "$ssh_target" "mkdir -p '$release'"
git archive "$revision" | ssh "$ssh_target" "tar -x -C '$release'"
ssh "$ssh_target" bash -s -- "$revision" <<'REMOTE'
set -euo pipefail
root="$HOME/prefect-edge"
release="$root/releases/$1"
ln -sfn "$release" "$root/current"
install -m 0644 "$release"/deploy/prefect-edge/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now prefect-hub
systemctl --user is-active prefect-hub
REMOTE

#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
sha=$(git rev-parse HEAD)
if ! git diff --quiet HEAD -- packages/mps/src/mps/bandwidth_watchdog.py deploy/bandwidth-watchdog scripts/deploy_bandwidth_watchdog.sh; then
    echo 'Commit watchdog changes before deploying.' >&2
    exit 1
fi
release="/home/stoat/bandwidth-watchdog/releases/$sha"
ssh heavypad "mkdir -p '$release'"
scp packages/mps/src/mps/bandwidth_watchdog.py "heavypad:$release/watchdog.py"
scp deploy/bandwidth-watchdog/{pyproject.toml,uv.lock,config.json,bandwidth-watchdog.service} "heavypad:$release/"
ssh heavypad bash -s -- "$release" <<'REMOTE'
set -euo pipefail
release=$1
cd "$release"
/home/stoat/.local/bin/uv sync --offline --frozen --no-dev
mkdir -p "$HOME/.config/bandwidth-watchdog" "$HOME/.config/systemd/user" "$HOME/.local/state/bandwidth-watchdog"
if [ ! -f "$HOME/.config/bandwidth-watchdog/config.json" ]; then
    install -m 0600 config.json "$HOME/.config/bandwidth-watchdog/config.json"
fi
install -m 0644 bandwidth-watchdog.service "$HOME/.config/systemd/user/bandwidth-watchdog.service"
ln -sfn "$release" "$HOME/bandwidth-watchdog/current"
systemctl --user daemon-reload
systemctl --user enable bandwidth-watchdog.service
systemctl --user restart bandwidth-watchdog.service
systemctl --user is-active bandwidth-watchdog.service
REMOTE

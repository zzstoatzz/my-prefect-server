#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
sha=$(git rev-parse HEAD)
if ! git diff --quiet HEAD -- packages/mps/src/mps/alerts deploy/alerts-mcp scripts/deploy_alerts_mcp.sh; then
    echo 'Commit alerts MCP changes before deploying.' >&2
    exit 1
fi
release="/home/stoat/alerts-mcp/releases/$sha"
ssh heavypad "mkdir -p '$release/mps/alerts'"
scp packages/mps/src/mps/alerts/{__init__,model,server}.py "heavypad:$release/mps/alerts/"
scp deploy/alerts-mcp/{pyproject.toml,uv.lock,env,alerts-mcp.service} "heavypad:$release/"
ssh heavypad bash -s -- "$release" <<'REMOTE'
set -euo pipefail
release=$1
cd "$release"
touch mps/__init__.py
# The home connection is metered: use the package cache, and say so when it
# falls short. The whole locked set is about 17 MB of wheels.
if ! /home/stoat/.local/bin/uv sync --offline --frozen --no-dev 2>/dev/null; then
    echo 'package cache incomplete; downloading the missing wheels' >&2
    /home/stoat/.local/bin/uv sync --frozen --no-dev
fi
mkdir -p "$HOME/.config/alerts-mcp" "$HOME/.config/systemd/user"
install -m 0600 env "$HOME/.config/alerts-mcp/env"
install -m 0644 alerts-mcp.service "$HOME/.config/systemd/user/alerts-mcp.service"
ln -sfn "$release" "$HOME/alerts-mcp/current"
systemctl --user daemon-reload
systemctl --user enable alerts-mcp.service
systemctl --user restart alerts-mcp.service
systemctl --user is-active alerts-mcp.service
REMOTE

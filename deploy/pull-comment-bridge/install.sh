#!/usr/bin/env bash
# Build a persistent venv for a staged mps wheel on heavypad and point the
# pull-comment-bridge user unit at it. Run from the laptop:
#   bash deploy/pull-comment-bridge/install.sh <wheel sha256>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
sha="${1:?wheel sha256 (a directory under ~/phi-spike-worker/releases on heavypad)}"
host="${HEAVYPAD_SSH:-stoat@heavypad}"

ssh "$host" bash -s -- "$sha" <<'REMOTE'
set -euo pipefail
sha=$1
wheel="/home/stoat/phi-spike-worker/releases/$sha/mps-0.1.0-py3-none-any.whl"
test -f "$wheel"
root=/home/stoat/pull-comment-bridge
release="$root/releases/$sha"
mkdir -p "$release"
cat > "$release/pyproject.toml" <<PYPROJECT
[project]
name = "pull-comment-bridge-release"
version = "0"
requires-python = ">=3.13"
dependencies = ["mps @ file://$wheel"]

[tool.uv]
package = false
PYPROJECT
cd "$release"
UV_PROJECT_ENVIRONMENT="$release/venv" /home/stoat/.local/bin/uv sync --no-dev --python 3.14.2
"$release/venv/bin/python" -c 'import mps.pull_comment_bridge'
mkdir -p "$root/backups"
cp /home/stoat/.config/systemd/user/pull-comment-bridge.service \
  "$root/backups/pull-comment-bridge.$(date +%s).service" 2>/dev/null || true
if [ -L "$root/current" ]; then readlink "$root/current" > "$root/previous-release"; fi
ln -sfn "$release" "$root/current"
REMOTE

ssh "$host" 'cat > ~/.config/systemd/user/pull-comment-bridge.service' \
  < "$HERE/pull-comment-bridge.service"
ssh "$host" 'systemctl --user daemon-reload
  systemctl --user enable --now pull-comment-bridge
  systemctl --user restart pull-comment-bridge
  sleep 3
  systemctl --user is-active pull-comment-bridge
  curl -s -m 5 http://127.0.0.1:8791/health; echo'

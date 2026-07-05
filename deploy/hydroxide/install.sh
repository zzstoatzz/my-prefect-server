#!/usr/bin/env bash
# install hydroxide on heavypad and register the systemd unit.
# run as stoat from this directory (or scp the dir over first).
set -euo pipefail

VERSION=v0.2.32

if ! command -v go >/dev/null; then
  echo "go not found — installing via apt"
  sudo apt-get update && sudo apt-get install -y golang-go
fi

GOBIN=~/.local/bin go install "github.com/emersion/hydroxide/cmd/hydroxide@${VERSION}"
~/.local/bin/hydroxide -h >/dev/null 2>&1 || { echo "hydroxide binary not working"; exit 1; }

sudo cp hydroxide.service /etc/systemd/system/hydroxide.service
sudo systemctl daemon-reload
sudo systemctl enable hydroxide

cat <<'EOF'

next (interactive, needs your proton password):

  ~/.local/bin/hydroxide auth <your-proton-username>

it prints a BRIDGE PASSWORD — save it, then:

  sudo systemctl start hydroxide

and create the Prefect Secret block (from the my-prefect-server repo root):

  uv run python -c "
  from prefect.blocks.system import Secret
  Secret(value={'username': '<proton-username>', 'password': '<bridge-password>'}).save('proton-bridge-creds')
  "

(remember: point PREFECT_API_URL/PREFECT_API_AUTH_STRING at prod first — `just` recipes load .env)
EOF

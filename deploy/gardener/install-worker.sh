#!/usr/bin/env bash
set -euo pipefail
revision=$(git rev-parse --verify "${1:-HEAD}^{commit}")
release="/home/stoat/gardener-exe-worker/releases/$revision"
ssh stoat@heavypad "mkdir -p '$release'"
git archive "$revision" | ssh stoat@heavypad "tar -x -C '$release'"
ssh stoat@heavypad bash -s -- "$revision" <<'REMOTE'
set -euo pipefail
revision=$1
root=/home/stoat/gardener-exe-worker
release="$root/releases/$revision"
cd "$release"
UV_PROJECT_ENVIRONMENT="$release/venv" /home/stoat/.local/bin/uv sync \
    --locked --package mps --package prefect-exe --no-dev --no-editable --python 3.13.7
"$release/venv/bin/python" -c 'import mps.exe_worker, prefect_exe'
"$release/venv/bin/python" - <<'PY'
import asyncio
from pathlib import Path
from prefect_exe.client import ExeClient
async def check():
    async with ExeClient(Path('/home/stoat/.ssh/exe_gardener_ed25519')) as client:
        if await client.list('prefect-7df0c11a-'):
            raise SystemExit('Worker has VMs; retry after they finish')
asyncio.run(check())
PY
printf '%s\n' "$revision" > "$release/SOURCE_REVISION"
mkdir -p "$root/backups"
cp /home/stoat/.config/systemd/user/pi-exe-worker.service "$root/backups/pi-exe-worker.$(date +%s).service"
if [ -L "$root/current" ]; then readlink "$root/current" > "$root/previous-release"; fi
ln -sfn "$release" "$root/current"
install -m 0644 deploy/gardener/pi-exe-worker.service /home/stoat/.config/systemd/user/pi-exe-worker.service
systemctl --user daemon-reload
systemctl --user restart pi-exe-worker
systemctl --user is-active pi-exe-worker
REMOTE

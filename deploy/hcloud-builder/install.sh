#!/usr/bin/env bash
set -euo pipefail
revision=$(git rev-parse --verify "${1:?pass the committed MPS revision}^{commit}")
release="/home/stoat/hcloud-builder/releases/$revision"
ssh stoat@heavypad "mkdir -p '$release'"
git archive "$revision" | ssh stoat@heavypad "tar -x -C '$release'"
ssh stoat@heavypad bash -s -- "$revision" <<'REMOTE'
set -euo pipefail
revision=$1
root=/home/stoat/hcloud-builder
release="$root/releases/$revision"
cd "$release"
test -s /home/stoat/.config/prefect-home/hcloud-builder.env
test -s /home/stoat/.config/prefect-home/gardener-tailnet.json
UV_PROJECT_ENVIRONMENT="$release/venv" /home/stoat/.local/bin/uv sync \
    --locked --package mps --package prefect-exe --no-dev --no-editable --python 3.13.7
"$release/venv/bin/python" -c 'import mps.hcloud_worker'
set -a
source /home/stoat/.config/prefect-home/hcloud-builder.env
set +a
"$release/venv/bin/python" - <<'PY'
import asyncio, os
from mps.hcloud_builder import HCloud
async def check():
    async with HCloud(os.environ['HCLOUD_TOKEN']) as api:
        if await api.owned_servers():
            raise SystemExit('Builder VMs still exist; drain before changing worker revision')
asyncio.run(check())
PY
printf '%s\n' "$revision" > "$release/SOURCE_REVISION"
if [ -L "$root/current" ]; then readlink "$root/current" > "$root/previous-release"; fi
ln -sfn "$release" "$root/current"
install -m 0644 deploy/hcloud-builder/hcloud-builder.service /home/stoat/.config/systemd/user/hcloud-builder.service
mkdir -p "$root/state/billing"
for type in service timer; do
    install -m 0644 "deploy/hcloud-builder/billing-offsite.$type" "/home/stoat/.config/systemd/user/hcloud-builder-billing-offsite.$type"
done
systemctl --user daemon-reload
systemctl --user enable --now hcloud-builder-billing-offsite.timer
systemctl --user enable --now hcloud-builder
systemctl --user restart hcloud-builder
systemctl --user is-active hcloud-builder
REMOTE

#!/usr/bin/env bash
set -euo pipefail
export DOCKER_HOST=unix:///var/run/docker.sock
object=${1:?pass the retirement object key}
expected=${2:?pass its recorded SHA256}
[[ "$object" =~ ^retirement/hetzner-prefect/[0-9]{8}T[0-9]{6}Z.tar.gz.age$ ]]
[[ "$expected" =~ ^[0-9a-f]{64}$ ]]
umask 077
work=$(mktemp -d)
container="prefect-retirement-restore-$$"
trap 'docker rm -f "$container" >/dev/null 2>&1 || true; rm -rf "$work"' EXIT
env_file="$HOME/.config/prefect-home/r2.env"
bucket=$(sed -n 's/^PREFECT_HOME_R2_BUCKET=//p' "$env_file")
test -n "$bucket"
rclone_image="rclone/rclone@sha256:2687085f718d3c628f7fdfb77c52a1d344332aed543110bb88088f2c70d43eb5"
postgres_image="postgres@sha256:16bc17c64a573ef34162af9298258d1aec548232985b33ed7b1eac33ba35c229"
docker run --rm --user "$(id -u):$(id -g)" --env-file "$env_file" \
  -v "$work:/work" "$rclone_image" copyto "r2:$bucket/$object" /work/archive.age
printf '%s  %s\n' "$expected" "$work/archive.age" | sha256sum --check --status
age -d -i "$HOME/.config/sops/age/keys.txt" "$work/archive.age" \
  | tar -xz --no-same-owner --no-same-permissions -C "$work"
python3 - "$work/control-plane" <<'PY'
import json,sqlite3,sys,tarfile
from pathlib import Path
root=Path(sys.argv[1])
manifest=json.loads((root/'manifest.json').read_text())
for name,size in manifest['files'].items():
    if (root/name).stat().st_size != size:raise RuntimeError('Restored file size differs: '+name)
with sqlite3.connect('file:'+str(root/'prefect-push.db')+'?mode=ro',uri=True) as db:
    assert db.execute('pragma integrity_check').fetchone()==('ok',)
with tarfile.open(root/'results.tar.gz') as archive:
    archive.extractall(root/'results-restored',filter='data')
print('Archive decrypted; file inventory, push SQLite and result extraction passed')
PY
docker run -d --rm --name "$container" --network none --memory 2g --cpus 1 \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=restore "$postgres_image" >/dev/null
for _ in $(seq 60); do
  docker exec "$container" pg_isready -U postgres -d restore -q 2>/dev/null && break
  sleep 1
done
docker exec "$container" pg_isready -U postgres -d restore -q
docker exec -i "$container" pg_restore -U postgres -d restore \
  --no-owner --no-acl --exit-on-error < "$work/control-plane/postgres.dump"
docker exec "$container" psql -U postgres -d restore -XAtq -v ON_ERROR_STOP=1 -c \
  "SELECT 'deployments=' || (SELECT count(*) FROM deployment) || ' flow_runs=' || (SELECT count(*) FROM flow_run) || ' blocks=' || (SELECT count(*) FROM block_document) || ' automations=' || (SELECT count(*) FROM automation);"
printf '%s restored %s sha256=%s\n' "$(date -u +%FT%TZ)" "$object" "$expected"

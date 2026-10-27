#!/usr/bin/env bash
# Restore the newest R2 copy into a throwaway, network-less PostgreSQL. This never
# touches the k3d cluster, and no Prefect server runs against the restored data.
set -euo pipefail
export DOCKER_HOST="${DOCKER_HOST:-unix:///var/run/docker.sock}"
state="$HOME/.local/state/prefect-home"
env_file="$HOME/.config/prefect-home/r2.env"
key="$HOME/.config/sops/age/keys.txt"
bucket=$(sed -n 's/^PREFECT_HOME_R2_BUCKET=//p' "$env_file")
[ -n "$bucket" ]
remote="r2:$bucket/postgres"
rclone_image="rclone/rclone@sha256:2687085f718d3c628f7fdfb77c52a1d344332aed543110bb88088f2c70d43eb5"
postgres_image="postgres@sha256:16bc17c64a573ef34162af9298258d1aec548232985b33ed7b1eac33ba35c229"
expected_deployments="
75c6a70d-2fcb-46c5-adc9-09522f6f2aea
815707b1-4540-40d2-88b9-cdd7c2cf154e
862675be-a319-4d1f-aeeb-2fa43980be86
05b19ef0-225e-4a4e-96af-e28971d5f91a
8e8113ce-4d7a-4969-a7b4-08979bb9452c
c85b78c7-116f-4433-b001-d3bf47d9fefb
32b67ef3-f639-46aa-8b93-2ab6e0c463f0
7f0c3fc7-cd6f-4a22-bf83-fb6e606a624d
ad73bd44-c4b6-4c83-9db3-3b4294952e43
0391974c-686d-4332-a051-efb1eb9dc55a
af3781c0-020f-45eb-90de-801aca6e9786
ef81bea3-f5f6-4e9e-8d02-616571fdfdd1
7056695e-b9b4-4729-b1d4-5b67a1fdb3b2
cabf0900-4a3f-4137-ae8c-df5bacc46e5d
9bc72ce6-5adb-43c5-87ed-9e6a48329176
3b13d70f-bd6f-458f-925a-d7329db1149a
787a3105-4c83-428d-a6f5-b4cea00ecbda
7d1b713b-b283-4662-88b9-9b77dd9c606a"
max_gap_minutes=120

umask 077
work=$(mktemp -d)
container="prefect-home-restore-verify-$$"
trap 'docker rm -f "$container" >/dev/null 2>&1 || true; rm -rf "$work"' EXIT
rclone() {
  docker run --rm --user "$(id -u):$(id -g)" --env-file "$env_file" \
    -v "$work:/work" "$rclone_image" "$@"
}

name="${1:-$(rclone lsf "$remote" --include '/*.dump.age' | sort | tail -1)}"
[[ "$name" =~ ^[0-9]{8}T[0-9]{6}Z\.dump\.age$ ]]
rclone copyto "$remote/$name" "/work/$name"
rclone check /work "$remote" --one-way --include "/$name"
sha=$(sha256sum "$work/$name" | cut -d' ' -f1)
if [ -f "$state/backups/$name" ] \
  && [ "$(sha256sum "$state/backups/$name" | cut -d' ' -f1)" != "$sha" ]; then
  echo "R2 copy of $name differs from the local dump" >&2
  exit 1
fi

started=$(date -u +%s)
docker run -d --rm --name "$container" --network none --memory 2g --cpus 1 \
  --tmpfs /var/lib/postgresql/data:rw,size=4g \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=restore \
  "$postgres_image" >/dev/null
for _ in $(seq 60); do
  docker exec "$container" pg_isready -U postgres -d restore -q 2>/dev/null && break
  sleep 1
done
docker exec "$container" pg_isready -U postgres -d restore -q

age -d -i "$key" "$work/$name" \
  | docker exec -i "$container" pg_restore -U postgres -d restore --no-owner --no-acl --exit-on-error

sql() { docker exec "$container" psql -U postgres -d restore -XAtq -v ON_ERROR_STOP=1 -c "$1"; }
ids=$(printf "'%s'," $expected_deployments)
missing=$(sql "SELECT count(*) FROM unnest(ARRAY[${ids%,}]::uuid[]) AS e(id)
  WHERE NOT EXISTS (SELECT 1 FROM deployment d WHERE d.id = e.id)")
[ "$missing" = 0 ] || { echo "restored database lacks $missing expected deployments" >&2; exit 1; }

stamp="${name%.dump.age}"
gap=$(sql "SELECT floor(extract(epoch FROM to_timestamp('$stamp', 'YYYYMMDD\"T\"HH24MISS\"Z\"')::timestamp
  - max(end_time) AT TIME ZONE 'UTC') / 60) FROM flow_run WHERE state_type = 'COMPLETED'")
[ -n "$gap" ] && [ "$gap" -le "$max_gap_minutes" ] \
  || { echo "newest completed run is ${gap:-?} minutes before the dump" >&2; exit 1; }

counts=$(sql "SELECT 'deployments=' || (SELECT count(*) FROM deployment)
  || ' flow_runs=' || (SELECT count(*) FROM flow_run)
  || ' blocks=' || (SELECT count(*) FROM block_document)
  || ' automations=' || (SELECT count(*) FROM automation)
  || ' tables=' || (SELECT count(*) FROM pg_tables WHERE schemaname = 'public')")
at=$(date -u +%FT%TZ)
printf '%s ok %s sha256=%s %s last_completed_gap_min=%s seconds=%s\n' \
  "$at" "$name" "$sha" "$counts" "$gap" "$(( $(date -u +%s) - started ))" \
  | tee -a "$state/restore-verifications.log"
jq -n --arg at "$at" --arg name "$name" '{verified_at: $at, name: $name}' \
  >"$state/.restore-verified.json.tmp"
mv "$state/.restore-verified.json.tmp" "$state/restore-verified.json"

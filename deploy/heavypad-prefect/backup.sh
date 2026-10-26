#!/usr/bin/env bash
set -euo pipefail
export DOCKER_HOST=unix:///var/run/docker.sock
backup_dir="$HOME/.local/state/prefect-home/backups"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
exec 9>"$backup_dir/.lock"
flock -n 9 || exit 0
stamp=$(date -u +%Y%m%dT%H%M%SZ)
partial="$backup_dir/.$stamp.dump.age.partial"
trap 'rm -f "$partial"' EXIT
umask 077
docker exec k3d-prefect-home-server-0 kubectl -n prefect-home exec deployment/postgres -- \
  pg_dump -U prefect -d prefect -Fc | \
  age -r age195mg0pvtljlk2k66w4ng48y0ywxq6550mr6u574ad83k63ernc5qmgy8qw \
      -r age15dxn35sc73nw9cz75sf8wkm48zznexd6p0va80vn27jzu0m93ylsncse65 > "$partial"
test -s "$partial"
mv "$partial" "$backup_dir/$stamp.dump.age"
find "$backup_dir" -maxdepth 1 -type f -name '*.dump.age' -mtime +7 -delete
printf 'Encrypted HeavyPad server PostgreSQL backup: %s\n' "$backup_dir/$stamp.dump.age"

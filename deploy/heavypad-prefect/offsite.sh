#!/usr/bin/env bash
# Copy already-encrypted dumps to R2. Never deletes or overwrites; the bucket
# lock enforces the same, so a stolen token cannot erase retained copies.
set -euo pipefail
export DOCKER_HOST="${DOCKER_HOST:-unix:///var/run/docker.sock}"
state="$HOME/.local/state/prefect-home"
backup_dir="$state/backups"
env_file="$HOME/.config/prefect-home/r2.env"
bucket=$(sed -n 's/^PREFECT_HOME_R2_BUCKET=//p' "$env_file")
[ -n "$bucket" ]
remote="r2:$bucket/postgres"
image="rclone/rclone@sha256:2687085f718d3c628f7fdfb77c52a1d344332aed543110bb88088f2c70d43eb5"
rclone() {
  docker run --rm --user "$(id -u):$(id -g)" --env-file "$env_file" \
    -v "$backup_dir:/data:ro" "$image" "$@"
}
exec 9>"$state/.offsite.lock"
flock -n 9 || exit 0
umask 077

rclone copy /data "$remote" --include '/*.dump.age' --immutable --checksum \
  --no-update-modtime --s3-no-check-bucket
rclone check /data "$remote" --one-way --include '/*.dump.age'
newest=$(rclone lsf "$remote" --include '/*.dump.age' | sort | tail -1)
[ -n "$newest" ]
jq -n --arg at "$(date -u +%FT%TZ)" --arg newest "$newest" \
  '{checked_at: $at, newest_uploaded: $newest}' >"$state/.offsite.json.tmp"
mv "$state/.offsite.json.tmp" "$state/offsite.json"
printf 'R2 holds every local dump; newest %s\n' "$newest"

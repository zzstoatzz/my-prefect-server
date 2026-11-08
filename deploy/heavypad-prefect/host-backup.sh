#!/usr/bin/env bash
# Encrypted snapshot of HeavyPad host state that lives in no repository and no
# PostgreSQL dump: unit files, env files, service state, Redis AOF, cluster
# manifests, tunnel credentials, and live captures (Tailscale serve, packages).
# Uploaded to R2 beside the PostgreSQL dumps; R2 only ever holds ciphertext.
set -euo pipefail
export DOCKER_HOST="${DOCKER_HOST:-unix:///var/run/docker.sock}"
state="$HOME/.local/state/prefect-home"
out_dir="$state/host-backups"
env_file="$HOME/.config/prefect-home/r2.env"
key="$HOME/.config/sops/age/keys.txt"
bucket=$(sed -n 's/^PREFECT_HOME_R2_BUCKET=//p' "$env_file")
[ -n "$bucket" ]
remote="r2:$bucket/host"
rclone_image="rclone/rclone@sha256:2687085f718d3c628f7fdfb77c52a1d344332aed543110bb88088f2c70d43eb5"
recipients=(
  age195mg0pvtljlk2k66w4ng48y0ywxq6550mr6u574ad83k63ernc5qmgy8qw
  age15dxn35sc73nw9cz75sf8wkm48zznexd6p0va80vn27jzu0m93ylsncse65
)
age_args=()
for r in "${recipients[@]}"; do age_args+=(-r "$r"); done
kube() { docker exec k3d-prefect-home-server-0 kubectl -n prefect-home "$@"; }
rclone() {
  docker run --rm --user "$(id -u):$(id -g)" --env-file "$env_file" \
    -v "$out_dir:/data:ro" "$rclone_image" "$@"
}

mkdir -p "$out_dir"
chmod 700 "$out_dir"
exec 9>"$state/.host-backup.lock"
flock -n 9 || exit 0
umask 077
stamp=$(date -u +%Y%m%dT%H%M%SZ)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cap="$work/captured"
mkdir -p "$cap"

systemctl --user list-unit-files --state=enabled --no-legend >"$cap/enabled-user-units.txt"
systemctl --user list-timers --all --no-pager >"$cap/user-timers.txt"
systemctl list-units --type=service --state=running --no-legend --no-pager >"$cap/running-system-units.txt"
cat /etc/systemd/system/spindle.service /etc/systemd/system/spindle-nix-cache.service \
  /etc/systemd/system/spindle.service.d/* >"$cap/spindle-system-units.txt" 2>/dev/null || :
tailscale serve status --json >"$cap/tailscale-serve.json"
tailscale status --json >"$cap/tailscale-status.json"
crontab -l >"$cap/crontab.txt" 2>/dev/null || :
dpkg --get-selections >"$cap/dpkg-selections.txt"
ls -la "$HOME/.local/bin" >"$cap/local-bin.txt"
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' >"$cap/docker-ps.txt"
docker images --digests --format '{{.Repository}}\t{{.Tag}}\t{{.Digest}}' >"$cap/docker-images.txt"
kube get all,pvc,configmap,secret -o yaml >"$cap/prefect-home-k8s.yaml"
kube exec deployment/redis -- redis-cli INFO persistence >"$cap/redis-info-persistence.txt"
kube exec deployment/redis -- tar -C /data -cf - . >"$cap/redis-data.tar"

paths=(
  .config .cloudflared .local/state
  .local/share/prefect-home .local/share/heavypad-gpu-exporter
  .local/share/heavypad-unit-sweep .local/share/albedo-phone
  .local/share/heavypad-studio/app
  phi-inference/state backup secrets home-mcp/home/deploy
)
while IFS= read -r f; do
  f="${f#"$HOME"/}"
  covered=0
  for p in "${paths[@]}"; do [[ "$f" == "$p"/* ]] && covered=1; done
  [ "$covered" = 1 ] || paths+=("$f")
done < <(
  grep -hoE '^EnvironmentFile=-?\S+' "$HOME/.config/systemd/user/"*.service \
    | sed "s/^EnvironmentFile=-\?//; s|%h|$HOME|" | sort -u)
existing=()
for p in "${paths[@]}"; do
  [ -e "$HOME/$p" ] && existing+=("$p") || echo "skipping missing $p" >&2
done

partial="$out_dir/.$stamp.host.tar.age.partial"
trap 'rm -f "$partial"; rm -rf "$work"' EXIT
tar -czf - \
  --exclude='./.config/Docker Desktop' --exclude=./.config/libreoffice \
  --exclude=./.config/nvim --exclude=./.config/pulse --exclude=./.config/ibus \
  --exclude=./.config/go --exclude=./.config/opencode --exclude=./.config/dconf \
  --exclude='./.config/gtk-*' --exclude=./.config/chromium \
  --exclude=./.local/state/prefect-home/backups \
  --exclude=./.local/state/prefect-home/migration-backups \
  --exclude=./.local/state/prefect-home/host-backups \
  --exclude=./.local/share/prefect-home/registration \
  --exclude=./.local/share/prefect-home/results \
  --exclude=./.local/share/prefect-home/storage \
  --exclude=./.local/share/prefect-home/tools \
  --exclude=./.local/share/prefect-home/bin \
  --exclude=./.local/share/heavypad-studio/app/.venv \
  --exclude=./.local/share/heavypad-studio/app/node_modules \
  -C "$HOME" "${existing[@]/#/./}" -C "$work" ./captured \
  | age "${age_args[@]}" >"$partial"
test -s "$partial"
age -d -i "$key" "$partial" | tar -tzf - >/dev/null
mv "$partial" "$out_dir/$stamp.host.tar.age"
find "$out_dir" -maxdepth 1 -type f -name '*.host.tar.age' -mtime +7 -delete

rclone copy /data "$remote" --include '/*.host.tar.age' --immutable --checksum \
  --no-update-modtime --s3-no-check-bucket
rclone check /data "$remote" --one-way --include '/*.host.tar.age'
newest=$(rclone lsf "$remote" --include '/*.host.tar.age' | sort | tail -1)
[ "$newest" = "$stamp.host.tar.age" ]
jq -n --arg at "$(date -u +%FT%TZ)" --arg name "$newest" \
  --argjson bytes "$(stat -c %s "$out_dir/$newest")" \
  '{checked_at: $at, newest_uploaded: $name, bytes: $bytes}' >"$state/.host-backup.json.tmp"
mv "$state/.host-backup.json.tmp" "$state/host-backup.json"
printf 'Host state snapshot %s (%s bytes) decrypts, lists, and is in R2\n' \
  "$newest" "$(stat -c %s "$out_dir/$newest")"

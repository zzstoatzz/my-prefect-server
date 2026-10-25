#!/usr/bin/env bash
set -euo pipefail
commit=${1:?pass the committed MPS revision}
volume=${2:?pass the attached Hetzner volume ID}
[[ "$commit" =~ ^[0-9a-f]{40}$ && "$volume" =~ ^[0-9]+$ ]]
mount_path=$(findmnt -n -o TARGET -S "/dev/disk/by-id/scsi-0HC_Volume_$volume")
[[ -n "$mount_path" && "$mount_path" == /mnt/HC_Volume_* ]]
test -s /etc/typeahead-cloud-worker.env
apt-get update -qq
apt-get install -y -qq rclone curl git xz-utils ca-certificates
if ! command -v uv >/dev/null; then
  curl --fail --silent --show-error --location https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
fi
id typeahead-indexer >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/typeahead-indexer --shell /usr/sbin/nologin typeahead-indexer
chown typeahead-indexer:typeahead-indexer "$mount_path"
ln -sfn "$mount_path" /var/lib/typeahead-indexer/builds
install -d -o typeahead-indexer -g typeahead-indexer /var/lib/typeahead-indexer/.local /var/lib/typeahead-indexer/.local/opt
zig_dir=/var/lib/typeahead-indexer/.local/opt/zig-x86_64-linux-0.17.0
if [ ! -x "$zig_dir/zig" ]; then
  archive=$(mktemp)
  trap 'unlink "$archive"' EXIT
  curl --fail --silent --show-error --location https://ziglang.org/download/0.17.0/zig-x86_64-linux-0.17.0.tar.xz -o "$archive"
  printf '%s  %s\n' 1cbe9df9f27e6b78d14ccbca43b6703a404ef79ef1c463de901d7f088d4e2026 "$archive" | sha256sum --check --status
  tar -xJf "$archive" -C /var/lib/typeahead-indexer/.local/opt
  chown -R typeahead-indexer:typeahead-indexer "$zig_dir"
fi
runuser -u typeahead-indexer -- "$zig_dir/zig" version
sed -e "s|@MPS_COMMIT@|$commit|g" -e "s|@BUILD_MOUNT@|$mount_path|g" "$(dirname "$0")/prefect-typeahead-worker.service" > /etc/systemd/system/prefect-typeahead-worker.service
systemd-analyze verify /etc/systemd/system/prefect-typeahead-worker.service
systemctl daemon-reload
systemctl enable --now prefect-typeahead-worker.service

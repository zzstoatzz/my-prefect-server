#!/usr/bin/env bash
# Runs a command (normally `albedo ...`) in a bwrap sandbox on heavypad. The
# sandbox sees /nix, /usr and /etc read-only, plus two writable directories
# that stand in for the home directory: $ALBEDO_SANDBOX/home (albedo's own
# state and provider credentials) and $ALBEDO_SANDBOX/work (the job's inputs
# and outputs). Nothing else of the real home is visible: no sops keys, no
# prod-worker env, no other checkouts. Network is shared, because the model
# provider is remote.
set -euo pipefail

sandbox=${ALBEDO_SANDBOX:-$HOME/albedo-sandbox}
albedo_bin=$(dirname "$(readlink -f "$HOME/.local/bin/albedo")")

exec bwrap --unshare-all --share-net --die-with-parent --new-session \
  --ro-bind /nix /nix --ro-bind /usr /usr --ro-bind /etc /etc \
  --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/bin /bin --symlink usr/sbin /sbin \
  --tmpfs /tmp --proc /proc --dev /dev \
  --ro-bind /run/systemd/resolve /run/systemd/resolve \
  --bind "$sandbox/home" /home/stoat --bind "$sandbox/work" /home/stoat/work \
  --clearenv --setenv HOME /home/stoat --setenv PATH "$albedo_bin:/usr/bin:/bin" \
  --setenv TERM xterm-256color --setenv LANG C.UTF-8 \
  --chdir /home/stoat/work \
  "$@"

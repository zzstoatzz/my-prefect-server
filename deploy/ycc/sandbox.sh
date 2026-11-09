#!/usr/bin/env bash
# Runs a command (normally `ycc ...`) in a bwrap sandbox on heavypad. Same
# shape as deploy/albedo-sandbox/sandbox.sh: /nix, /usr and /etc read-only,
# plus $YCC_SANDBOX/home standing in for the home directory (ycc's config,
# secrets store, daemon state and token) and $YCC_SANDBOX/work for the
# projects the daemon serves. Nothing else of the real home is visible.
# ycc confines only its reviewers' shells, so this wrapper is what confines
# the coordinator and implementer. Network is shared: the model is remote.
set -euo pipefail

sandbox=${YCC_SANDBOX:-$HOME/ycc}
ycc_bin=$(dirname "$(readlink -f "$HOME/.local/bin/ycc")")

exec bwrap --unshare-all --share-net --die-with-parent --new-session \
  --ro-bind /nix /nix --ro-bind /usr /usr --ro-bind /etc /etc \
  --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/bin /bin --symlink usr/sbin /sbin \
  --tmpfs /tmp --proc /proc --dev /dev \
  --ro-bind /run/systemd/resolve /run/systemd/resolve \
  --bind "$sandbox/home" /home/stoat --bind "$sandbox/work" /home/stoat/work \
  --ro-bind "$ycc_bin" /opt/ycc \
  --clearenv --setenv HOME /home/stoat --setenv PATH "/opt/ycc:/usr/bin:/bin" \
  --setenv TERM xterm-256color --setenv LANG C.UTF-8 \
  --chdir /home/stoat/work \
  "$@"

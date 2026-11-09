#!/usr/bin/env bash
# Build ycc on heavypad at the pinned commit and link it at ~/.local/bin/ycc.
# Run on heavypad:  bash deploy/ycc/build.sh
# go.mod asks for a newer toolchain than the nix profile's go; GOTOOLCHAIN=auto
# lets go fetch it into the module cache.
set -euo pipefail

YCC_REV="${YCC_REV:-e0602905b7b6e97ef33c6e9b55220b04bdaeb188}"
GO="${GO:-/nix/var/nix/profiles/default/bin/go}"
dest="$HOME/.local/opt/ycc/${YCC_REV:0:7}"

mkdir -p "$dest" "$HOME/.local/bin"
GOBIN="$dest" GOTOOLCHAIN=auto "$GO" install -trimpath "github.com/whyrusleeping/ycc/cmd/ycc@${YCC_REV}"
ln -sfn "$dest/ycc" "$HOME/.local/bin/ycc"
readlink -f "$HOME/.local/bin/ycc"

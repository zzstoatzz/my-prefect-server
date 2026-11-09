#!/usr/bin/env bash
# Run a ycc client command against the heavypad daemon, from inside the
# sandbox. A bare `ycc` there looks for a daemon on its default port, which
# another service holds on heavypad, and then runs a throwaway daemon of its
# own; an explicit --addr needs the token passed too.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
port="${YCC_PORT:-4327}"

exec "$here/sandbox.sh" bash -c \
  'YCC_TOKEN=$(cat ~/.local/state/ycc/daemon-token) exec ycc --addr "http://127.0.0.1:$0" "$@"' "$port" "$@"

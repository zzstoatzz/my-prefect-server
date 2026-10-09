#!/bin/sh
# uv only runs as a short-lived installer here so the long-lived worker
# never holds the uv cache lock (it blocked `uv cache prune` for 5 minutes).
set -eu
set -a; . "$HOME/.config/prefect-laptop-worker/env"; set +a
uv tool install --upgrade --quiet prefect
exec "$HOME/.local/share/uv/tools/prefect/bin/prefect" worker start \
  --pool laptop-pool --type process --name laptop --with-healthcheck

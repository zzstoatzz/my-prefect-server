#!/usr/bin/env bash
set -euo pipefail
umask 077

cd "$(dirname "$0")/../.."
RELEASE_DIR="$(pwd -P)"
export PATH=/home/stoat/.local/bin:$PATH
export DAGSTER_HOME=/home/stoat/.local/share/dagster-hub
export ANALYTICS_DB_PATH=/home/stoat/prefect-analytics/analytics.duckdb
export LLM_SPEND_LOG_PATH=/home/stoat/prefect-analytics/llm-spend.jsonl

uv sync --frozen --python 3.13.11 --extra dagster --no-dev
if [[ -f "$DAGSTER_HOME/dagster.yaml" ]]; then
  uv run --no-sync python - <<'PY'
import dagster as dg
with dg.DagsterInstance.get() as instance:
    active = [run for run in instance.get_runs() if not run.is_finished]
    if active:
        raise SystemExit('Wait for active Dagster runs before changing the release')
PY
fi
uv run --no-sync dbt parse --no-partial-parse \
  --project-dir "$RELEASE_DIR/analytics" --profiles-dir "$RELEASE_DIR/analytics/profiles"
uv run --no-sync python - <<'PY'
import dagster as dg
from hub_dagster.definitions import defs
dg.Definitions.validate_loadable(defs)
PY
mkdir -p "$DAGSTER_HOME" /home/stoat/.config/systemd/user
cp deploy/dagster/dagster.yaml "$DAGSTER_HOME/dagster.yaml"
uv run --no-sync dagster instance migrate
ln -sfn "$RELEASE_DIR" /home/stoat/dagster-hub/current
cp deploy/dagster/dagster-hub-*.service /home/stoat/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable dagster-hub-web dagster-hub-daemon
systemctl --user restart dagster-hub-web dagster-hub-daemon

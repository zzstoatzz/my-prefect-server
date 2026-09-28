# Hub dbt on Dagster

Dagster owns execution, materializations, schema/column lineage, and the hub export
for the complete dbt project in `analytics/`. It runs on HeavyPad, beside the
analytics database. The existing Prefect `transform` deployment is now only a
handoff: it submits a Dagster run and waits for success. Its completion still
triggers `brief` and `phi-memory-synthesis`; ingest and classification are unchanged.

The Prefect flow UUID is also the Dagster run UUID. A retry reattaches to that run
rather than building twice, and a Dagster failure fails the handoff. No downstream
completion event is emitted until Dagster has built all models and atomically
exported `hub.duckdb`.

## Hosting and limits

- User services `dagster-hub-web` and `dagster-hub-daemon`, running as `stoat`.
  The existing lingering user manager starts them on boot.
- UI listens on `127.0.0.1:3030` only. There is no public ingress.
- Daemon/run cgroup: `CPUQuota=400%`, `MemoryHigh=4G`, `MemoryMax=8G`.
  Webserver: `MemoryHigh=1G`, `MemoryMax=2G`.
- Persistent local run/event storage: `~/.local/share/dagster-hub`.
- Dagster queue allows one run. The job uses an in-process executor so one
  `analytics-duckdb-writer` Prefect lease covers spend import, every dbt model,
  metadata queries, and export. This also serializes with existing ingest/docket
  writers. Dagster still needs the Prefect API for that shared lock.
- Run monitoring enforces a 25-minute maximum; the handoff waits up to 28 minutes.
- The 3 GiB analytics file stays at home. Only the existing slim hub export and
  append-only spend log sync to Hetzner. That sync now caps transfer at 256 KiB/s
  and skips overlapping invocations. The public hub continues serving at Hetzner.

```sh
ssh -N -L 3030:127.0.0.1:3030 stoat@heavypad
# open http://localhost:3030
ssh stoat@heavypad 'systemctl --user status dagster-hub-web dagster-hub-daemon'
```

## Release

The current source checkout/release is `/home/stoat/dagster-hub/current`.
Release directories contain this repository's `analytics`, `hub_dagster`,
`flows`, `packages`, lockfile, and deployment files, with no `.env` files.
From a prepared release directory on HeavyPad, run `bash deploy/dagster/install.sh`.
It installs the frozen `dagster` extra with Python 3.13.11, parses the manifest,
validates definitions, switches the release symlink, and starts the services.
It refuses to switch while a Dagster run is active. Deploy only while the Prefect
handoff is idle too. Future Dagster source changes require a release installation;
registering Prefect deployments alone does not update this local code.

Services read the existing worker environment file for Prefect connection/auth;
no copied credentials or additional secret store is introduced. `prefect.yaml`
points only the `transform` deployment at this installed environment. Other flows
retain their existing delivery paths.

## Verification

`validate_snapshot.py` takes a consistent, local copy while holding the existing
writer lease, then compares a normal complete dbt build against Dagster using the
same input snapshot. It compares all rows, including duplicates, across every
model and seed plus spend. It checks materialization schema metadata and exported
table coverage. Copies and private contents never leave HeavyPad. Run with the
worker environment loaded and production `ANALYTICS_DB_PATH`/
`LLM_SPEND_LOG_PATH`, passing a new private output directory:

```sh
uv run --no-sync python deploy/dagster/validate_snapshot.py ~/dagster-hub/validation-YYYYMMDD
```

Use `uv run --extra dagster pytest tests/test_dagster_hub.py` for failure/retry
regressions. A deliberately broken real dbt build must leave the published export
unchanged. Run the repository's normal checks before releasing changes.

## Recovery

A failed Dagster build leaves the last successfully exported hub database serving.
Inspect the shared run UUID in Dagster and Prefect. An ordinary Prefect retry
reattaches to that same run; use a new Prefect run for a corrected rebuild.

For rollback, wait for active Dagster runs to finish, restore the prior Prefect
transform deployment's `path`, `entrypoint`, `pull_steps`, and `job_variables`,
then stop the two Dagster services. Preserve Dagster storage and the current
analytics database. The existing writer lease also protects overlapping old/new
processes, but draining first is required for a clean handoff.

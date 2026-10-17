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
- Daemon/run cgroup: `MemoryHigh=4G`, `MemoryMax=8G`. The HeavyPad-specific unit
  pins the daemon and its children to efficiency CPU 16 with `Nice=10`.
  Recheck topology before using that unit on other hardware. The previous
  `CPUQuota=400%` was not enforced: the user manager delegates only memory/pids,
  so child cgroups have no CPU controller. Affinity bounds placement/concurrency,
  not CPU duty cycle.
  Webserver: `MemoryHigh=1G`, `MemoryMax=2G`.
- Persistent local run/event storage: `~/.local/share/dagster-hub`.
- Dagster queue allows one run. The job uses an in-process executor so one
  `analytics-duckdb-writer` Prefect lease covers spend import, every dbt model,
  metadata queries, and export. This also serializes with existing ingest/docket
  writers. Dagster still needs the Prefect API for that shared lock.
- Run monitoring enforces a 25-minute maximum; the handoff waits up to 28 minutes.
- Spend-log ingestion validates the same JSONL records, then writes a private normalized batch
  for DuckDB’s native JSON reader and one upsert instead of executing one statement per historical event.
  Its connection uses one execution thread. Duplicate IDs are resolved after
  SQL casts by log order, preserving last-event-wins behavior, including replay
  against existing rows. A failed batch rolls back as a unit. Full-log scanning
  remains deliberate; there is no checkpoint that can skip rewritten events.
- The 3 GiB analytics file stays at home. Only the existing slim hub export and
  append-only spend log sync to Hetzner. That sync now caps transfer at 256 KiB/s
  and skips overlapping invocations. The public hub continues serving at Hetzner.

```sh
ssh -N -L 3030:127.0.0.1:3030 stoat@heavypad
# open http://localhost:3030
ssh stoat@heavypad 'systemctl --user status dagster-hub-web dagster-hub-daemon'
```

## Python compatibility

The locked dbt stack supports Python 3.13 and 3.14. dbt Core must be at least
1.12.5; the former `dbt-adapters<1.24` workaround prevented resolving this stack
and left Mashumaro 3.14, which fails during import on Python 3.14. The lock now
uses dbt-adapters 1.24.5 and Mashumaro 3.17. dbt's
[Python 3.14 support](https://github.com/dbt-labs/dbt-core/pull/12828) includes the
required serialization changes.

Use `uv run --python 3.14 --extra dagster pytest -q` for the CI runtime and
`uv run --python 3.13 --extra dagster pytest tests/test_dagster_hub.py tests/test_dbt_runtime.py -q`
for the production runtime. These exercise real dbt parsing, builds, failure
propagation, and schema metadata; no serializer dependency overrides are needed.

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

Verified on HeavyPad on 2026-09-28:

- Shadow run `176bd58f-9acf-4cba-824a-b52cb362dd6f`: exact multiset equality for
  all 13 models, the seed, 140,020 spend records, and all three exported tables.
  Every dbt asset has observed column schema; all 13 models have column lineage.
- Production run `dd92f563-a780-4c33-9563-5112e4736640`: all 16 assets
  materialized, Prefect completed, and retrying the bridge reused the finished
  run. The dbt step took 15 seconds; total runtime was about seven minutes,
  dominated by the former row-at-a-time spend-log import (replaced below).
- The HeavyPad and Hetzner hub exports have identical SHA-256 hashes after sync.
- A failed production seed build prevented publication and downstream triggers.
  Absolute manifest paths and disabling stale partial parses fix that failure;
  a real seed regression reproduces the relative-path cache scenario.
- The full Python suite passed (507 tests), followed by all seven migration
  regressions after the final fixes. Ruff, formatting, types, deployment
  contracts, Svelte checks, and frontend lint passed.

## Recovery

A failed Dagster build leaves the last successfully exported hub database serving.
Inspect the shared run UUID in Dagster and Prefect. An ordinary Prefect retry
reattaches to that same run; use a new Prefect run for a corrected rebuild.

For rollback, wait for active Dagster runs to finish, restore the prior Prefect
transform deployment's `path`, `entrypoint`, `pull_steps`, and `job_variables`,
then stop the two Dagster services. Preserve Dagster storage and the current
analytics database. The existing writer lease also protects overlapping old/new
processes, but draining first is required for a clean handoff.

Bulk-import verification, October 9: the same 1,100-event input required 4.871 CPU
seconds with the reference importer and 0.070 with native JSON ingestion; every
output column matched in both directions. The complete 157,710-event log imported
in 5.040 CPU seconds (25.802 seconds elapsed under a 20% CPU duty cycle). These
are importer measurements, not full Dagster job timings. See
[evidence](evidence/bulk-spend.json) and `bench_spend.py` for reproduction.

Live release `/home/stoat/dagster-hub/releases/20261008-bulk-spend` was activated
after the previous Dagster run and Prefect handoff completed. Both services were
verified active and the daemon's actual affinity was CPU 16. A direct invocation
of the deployed importer against the production database, holding the existing
writer lease, processed 157,738 events in 9.607 CPU seconds. Wall time was 136.377
seconds under the quiet supervisor because other host workloads caused repeated
thermal pauses. This verified the installed importer and real database replay,
not a complete new Dagster job. See [live evidence](evidence/live-spend.json).

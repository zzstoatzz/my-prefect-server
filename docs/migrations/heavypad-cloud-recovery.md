# HeavyPad migration and Cloud recovery

## Operating model

HeavyPad owns migrated recurring schedules. Prefect Cloud is an explicitly
activated recovery destination, not a second scheduler. The original server
continues owning all unmigrated workflows, clients and state. A successful Cloud
run proves only the dependencies actually exercised; it does not make home
files, local services or running/paused jobs available remotely.

Before failover, fence the original owner so recovery cannot produce duplicates
when it returns. Reconcile in-flight effects and checkpoints before retrying;
never assume a lost control-plane connection means the process stopped.
Do not move shared DuckDB writers independently: server-side concurrency limits
on different instances do not coordinate. Transfer that cohort together or first
establish a lock shared by every runtime.

## State and availability assessment

Measured October 26, 2026 UTC: original PostgreSQL is 5496 MB, with approximately
3078 MB of events, 975 MB of task runs, 730 MB of task states and 594 MB of logs.
The three Prefect variables are `autofix_handled_comments`,
`fastmcp_briefed_threads`, and `fastmcp_notifications_last_modified`.
These are operational checkpoints/deduplication, not disposable history.
There are 27 original automations and 46 original deployments, 41 on home-pool.
An email-triage run was paused for input; finish/reconcile it on its original
server rather than recreate its approval on another instance.

Keep the new active database fresh. Preserve the old database with encrypted,
restore-tested archives before retirement. Transfer blocks, variables, limits,
automation references and required recent events with each workflow/cohort.
A full restore into the active new scheduler would reintroduce old schedules and
IDs; it is not a safe way to merge history. Archive restores must be isolated
from schedulers and external effects. The new PostgreSQL already has history,
so replacing it would also lose the migrated runs.

Original Redis used about 102 MiB and synchronous AOF reported healthy writes.
The event-persister, task-run-recorder, automations and actions_executor consumer
groups each had zero pending entries and zero lag at the observed instant.
Old broadcast consumer groups still exist; do not confuse them with outstanding
business actions or copy them wholesale. Redis holds streams, acknowledgments
and deduplication state. Keep the new broker fresh; at final retirement stop
producers, drain durable consumers, reconcile actions and stop the old owner.
An instantaneous empty queue today does not prove a future cutover is drained.

HeavyPad had 48 GiB available memory and about 994 GiB free disk. Most external
job traffic already originates there; home API/log traffic to the cloud should
become local, while remote workers would send orchestration traffic home.
Bandwidth under actual load still needs measurement. Home power/ISP failure now
also removes scheduling, orchestration and local monitoring. Before critical
migration: automatic off-host backups (currently manual copies), restore on a
second host, independent external health reporting, and a measured recovery
procedure. The current backup script protects PostgreSQL, not a coordinated
PostgreSQL/Redis/host-files snapshot.

## Qualified diagnostics batch

- Original deployment: `97f1446c-7265-4d31-81f9-57dbda460966`.
- HeavyPad deployment: `815707b1-4540-40d2-88b9-cdd7c2cf154e`.
- Cloud deployment: `44b7226b-cb4e-4817-9fad-68c036978ff3`.
- Source exercised on both runtimes: `aec7b992b842b448002790e2be25cc5c6ba74940`.
- HeavyPad manual run: `4d05cb2b-ad6e-4e44-b6b8-cdb64b80acd7`, Completed;
  hostname `heavypad`, artifact created, no expensive cache walk.
- Cloud managed run: `06adea58-1817-7eae-8000-697ddf3d052d`, Completed;
  remote EC2 runtime, Python 3.12.15, artifact created. Submission to completion
  took about 66 seconds; flow execution itself was under one second.
- Cloud needed only Prefect and the pinned public source. It did not install the
  full mps/dbt/ML dependency set and needed no home mount or credentials.
- HeavyPad uses `uv run --no-project --with prefect==3.8.2` for this flow;
  `--no-project` avoids inadvertently installing the entire repository project.

HeavyPad owns `37 * * * *` UTC; original source has `schedules: []`. Cloud source
has `schedules: []` and no recovery notification automation. Canary and backlog
remain independent. Deployment IDs/history were not replaced during registration.
Schedule ownership was applied from `a7a7ad4ef3ca80f8499bb6f7cb47cd97f0fc99f4`.
Registering the empty original source schedule did not clear its old live schedule;
the explicit CLI clear removed it and its queued jobs. Database readback then
found no Scheduled/Pending/Running/Cancelling original diagnostics runs.
HeavyPad queued 01:37, 02:37 and 03:37 UTC runs on its new queue; Cloud had none.
The first scheduled HeavyPad diagnostics run has not yet been observed; the real
manual run above is the execution qualification.

A Cloud diagnostics run measures its runtime, **not HeavyPad**. Kernel-wide
`/proc` measurements need not equal container allocation; cgroup and process
telemetry may be missing. This run reported `in_flight=None` because `pgrep`
was unavailable, and did not qualify host-process burn detection in managed
infrastructure. That is an explicit observability limitation, not home recovery.

`just heavypad-prefect-monitoring --apply` copies the existing `discord-alerts`
block and only `sustained burn -> discord` and `flow-run failure -> discord`.
The check mode compares block data by hash and rules/actions against the source;
notification run links use the HeavyPad URL. No synthetic Discord message was
sent. End-to-end notification delivery on the new server is not yet qualified.
The original automatic failure-to-autofix chain is intentionally not copied:
it would need to read failed runs from their new server and is a separate cohort.

No existing key was rotated. Notification configuration remains authoritative
in the original block for now: update there, reapply/check on HeavyPad. Before
original-server retirement remove that dependency and retain recoverable encrypted
configuration. Other HeavyPad auth/database rotation procedures remain in the
[server runbook](../../deploy/heavypad-prefect/README.md).

## Operations and rollback

```
just heavypad-prefect-monitoring
just heavypad-prefect deployment run diagnostics/diagnostics --watch
just cloud-recovery-prefect deployment run diagnostics/diagnostics-recovery --watch
```

Register the manual Cloud counterpart with a committed source pin:

```
MPS_SHA="$(git rev-parse HEAD)" just cloud-recovery-prefect --no-prompt deploy --prefect-file prefect.cloud-recovery.yaml --all
```

The Cloud wrapper pins the existing bot profile's account/workspace explicitly,
without changing the default profile. Its credential stays in that profile.
The Cloud MCP OAuth connection needs reauthentication; the CLI profile was
independently authenticated and used for the real managed run. Existing unrelated
Cloud pools, automations and deployments were left alone.

Rollback diagnostics by first disabling its HeavyPad schedule and cancelling
unstarted queued runs, waiting for any active run to finish, then restoring the
original :37 schedule in source and registering only that original deployment.
Verify exactly one recurring owner. Leave Cloud manual-only. Do not retire
HeavyPad notification rules while backlog still depends on failure alerts.

## Next migration candidates

1. `bufo-traffic`: dry-run first, then the single recurring owner; repeatable daily
   PDS upserts, a two-day lookback, Logfire and operator blocks, no DuckDB writer.
   Qualify secrets/plugin delivery and the remote write before claiming recovery.
2. `fleet-health` and `mcp-fleet-health`: preserve and verify the alerting chain,
   including the effect of residential versus managed network vantage points.
3. Defer DuckDB writers, event-dependent briefs, approvals, publishing and Gardener
   until their checkpoint, execution and effect ownership are specified.

## Downtime inventory

All 46 live original deployments are covered below. This is a source/configuration
assessment, not a destructive outage rehearsal. Unknowns block migration; no
blanket claim of staleness-only degradation is justified. In-flight operations
also differ from merely missing the next schedule. Revalidate the relevant row
and its upstream retention/deadlines before each migration.

| Group / deployments | Downtime consequence | Required state or runtime |
| --- | --- | --- |
| Sampling and visibility: `fastmcp-backlog`, `diagnostics`, `fleet-health`, `mcp-fleet-health` | Missed samples and alerts cannot be reconstructed as observations of the outage; workloads keep serving, but monitoring is blind. | Backlog JSONL; diagnostics host/cgroups; fleet network vantage; notification automations. |
| Repeatable rollups: `bufo-traffic`, `costs` | Displays become stale; bufo has a two-day lookback, so prolonged downtime can leave unfilled older buckets. No claim of unlimited backfill. | Provider credentials, PDS writes; external retention/backfill limits. |
| Serving snapshots: `pub-search-snapshot`, `leaflet-atlas`, `bisk-snapshot` | Existing published output remains, but becomes stale. Bisk uses round/day boundaries; a missed round is not equivalent to a later refresh. | R2/Pages, builder tools, local caches/state, publishing credentials. |
| Typeahead publication: `typeahead-index` | Existing index serves; publication freshness requires success within four days. Long outages can fail freshness and impair updates. | Cloud builder/toolchain/disk, generation and R2/rollout state; not a plain home job. |
| Typeahead enrichment: `typeahead-identity-hourly`, `typeahead-enrich-backfill`, `typeahead-plc-identity` | Identity/profile/moderation backlog grows while service runs; moderation staleness is a quality/safety consequence, not just a cosmetic chart. | Turso state, ingestion headroom, PLC persistent bundles (~30 GB), remote API limits. |
| Archive summaries: `strata-hourly` | Serving summaries lag. Catch-up depends on checkpoint and retained sealed segments; no unlimited outage guarantee. | SQLite checkpoint and filesystem lock, R2 state, archive keys; restore rather than implicit recrawl. |
| Notification and brief pipeline: `watch-fastmcp`, `fastmcp-brief` | Cursor/event/brief windows can lose timeliness or replay duplicates if reset. Brief reads a six-hour event window; API helpers currently use BasicAuth, not Cloud bearer auth. | Two Prefect variables, recent events, threshold automations, GitHub/LLM blocks and Discord. |
| Shared analytics pipeline: `ingest`, `classify-emails`, `transform`, `brief`, `phi-memory-synthesis` | Hub and memory projections lag; external event retention and pipeline triggers bound catch-up. Concurrent split control planes can corrupt serialization guarantees. | Shared analytics.duckdb writer cohort, result storage, local Proton bridge and Dagster, completion automations. |
| Interactive email: `email-backlog`, `email-triage` | Requests unavailable; paused triage has a response timeout and may expire. Replaying an approval or mail action is unsafe. | Local Proton bridge, mailbox UIDVALIDITY, DuckDB, run inputs/state and notification links. |
| Presence and lighting: `phone-presence` | Presence/lighting actions are unavailable; stale observations must not be replayed as current presence. | Private credential file, local light access, marker file, shared presence-writer limit. |
| Memory and atlas publication: `phi-tag-maintenance`, `phi-atlas`, `docket` | Maintenance/maps become stale; missed triggers and partially completed writes need reconciliation before retry. | TurboPuffer/PDS, embeddings/ML dependencies, DuckDB archives and completion triggers. |
| Time-sensitive bot passes: `phi-curation`, `phi-likes-review`, `phi-pull-review`, `phi-editorial`, `phi-character-retro`, `phi-chicken-scout`, `phi-chicken-precheck` | The bot service may stay online, but scheduled passes/reviews are missed. A pre-lock market check cannot be recovered by running it after the deadline. | Fly bot control token/endpoints, per-slot material, original automation references. |
| Coding and approval cohort: `watch-tangled-pulls`, `autofix`, `autofix-revise`, `investigate`, `pi-pr`, `pi-agent`, `merge-approved`, `test-pull-patch` | Remediation/reviews stop; running VMs or effects may continue after orchestration disconnect. Paused merge approval and failed-run IDs are not portable by re-registration. | Comment dedup variable, host bridge/wheels/tools, exe VMs, run history, credentials, explicit approval/fencing. |
| Native build gates: `dep-bump`, `stream-admission` | Build/adoption gates unavailable; do not bypass them or assume CI/deployment progress continues safely. | Native Zig/Docker/toolchains, host build resources, SSH/publishing credentials and external CI callers. |
| Manual record operations: `pds-records` | Manual actions unavailable; interrupted writes/deletes need reconciliation, not blind retry. | PDS credentials and action parameters; no automatic catch-up. |
| Unqualified external source: `mcp-atlas` | Deployment points at flows/crawl.py, absent from this repository. Availability and dependencies remain unqualified; block migration. | Recover its actual source/runtime contract before classifying or moving it. |

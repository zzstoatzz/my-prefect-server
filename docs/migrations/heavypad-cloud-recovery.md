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
migration: restore-tested automatic off-host backups (now enabled; see the runbook), restore on a
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
The first scheduled HeavyPad diagnostics run,
`c624fc2f-6424-4f29-a2ee-3de1ea3020ba`, completed on October 26 at
01:37:04 UTC in under one second.

A Cloud diagnostics run measures its runtime, **not HeavyPad**. Kernel-wide
`/proc` measurements need not equal container allocation; cgroup and process
telemetry may be missing. This run reported `in_flight=None` because `pgrep`
was unavailable, and did not qualify host-process burn detection in managed
infrastructure. That is an explicit observability limitation, not home recovery.

`just heavypad-prefect-monitoring --apply` copies the existing `discord-alerts`
block and the `sustained burn -> discord` and `flow-run failure -> discord` rules;
its later MCP batch also adds `fleet unhealthy -> discord`.
The check mode compares block data by hash and rules/actions against the source;
notification run links use the HeavyPad URL. After explicit operator approval,
one clearly labelled test alert exercised the real flow-failure pipeline on
October 26 at 01:20 UTC. Expected-Failed test run
`f0b893d9-1f3d-4bc8-ab46-4dd94700fe7c` emitted triggering event
`181809bb-473b-4a8d-8935-33b4c707acbb`; persisted audit event
`12836524-ca10-4051-96c8-9b1b45acba8f` recorded one successful
`prefect.automation.action.executed` with action type `send-notification`.
The triggering event was independently read back as this exact test run's Failed event. The observed audit window contained one successful action
and no failed actions; no retry or second synthetic message was sent. This
qualifies the shared notification transport and failure route. The sustained-burn
rule was matched against its source configuration, not separately fired.
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

The [October 26 deployment audit](../audits/2026-10-26-deployment-migration-audit.md)
checks all 46 original deployments against live run history and automations.
Nate approved omitting `pds-records`, `dep-bump`, and `pi-pr` from the migration;
retire those deployments with Hetzner. Their source can remain available.

1. `fleet-health`, `mcp-atlas` and `bisk-snapshot` moved to HeavyPad on October 26
   (below). Six Phi clock definitions are prepared inactive in the central
   HeavyPad file; none were registered or enabled as part of this batch. The existing HeavyPad
   worker has one flow slot, a 2 GiB cap and one CPU: isolate long builds before
   moving `pub-search-snapshot` or `leaflet-atlas` into that execution capacity.
2. Defer DuckDB writers, event-dependent briefs, approvals, other publishing and Gardener
   until their checkpoint, execution and effect ownership are specified.

Diagnostics, bufo traffic, public MCP health and costs now have real remote
execution receipts. This closes the selected low-risk portability batch, not
the remaining host-dependent migration or full home-outage recovery.

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
| External atlas crawl: `mcp-atlas` | The authoritative relay sweep reconciles deletions and long-tail records; the Worker's five-minute refresh is a separate path. Successful recent runs and source in `zzstoatzz.io/mcp-atlas` were verified in the October 26 audit. | Preserve the external repository and ingest credential; qualify publication and pin source before moving it. |

## Qualified bufo traffic batch

HeavyPad now owns the hourly :07 UTC publisher. The original deployment has no
schedule and no unstarted/active runs at cutover. Cloud has no schedule, defaults
to `dry_run=true`, and requires explicit parameters to publish. Both publishers
were exercised sequentially after fencing the original owner, never concurrently.
Schedule activation used source `060e24199afa2144d556262ee4457225abfe13f5`.
The first automatic run is queued for 02:07 UTC and has not yet been observed;
the completed manual publishing run is the runtime qualification.

| Resource | ID |
| --- | --- |
| Original deployment | `c60437fd-04b2-43f0-a63e-94e5d1910076` |
| HeavyPad deployment | `862675be-a319-4d1f-aeeb-2fa43980be86` |
| Cloud managed deployment | `46586b47-5a32-4afc-88c8-6c5124924826` |
| HeavyPad dry-run with artifact | `263f144a-2880-4350-a96e-d40127b03b33` |
| Cloud dry-run | `06adea89-f3aa-7082-8000-817f18c11ab1` |
| Cloud real publication | `06adea93-8e7d-7d58-8000-9747ccb4e516` |
| HeavyPad real publication | `9782727d-aedc-46e1-86eb-2ff5133e2e50` |

The successful runs exercised source `3af4e8f65a0866d2ed4c7650b38b2f55a480f6d8`.
Both dry-runs produced byte-identical full records after excluding `generatedAt`:
October 24 = 1227 requests, October 25 = 1164, October 26 = 279 at the observed
instant. These are historical samples, not current totals. Both publication runs
completed and wrote three daily records. After each, the public PDS records were
read back: artifact totals, search/image/5xx counts, all breakdown sums, and a
generation timestamp inside that run's start/end window matched. The later
HeavyPad write intentionally replaced the Cloud write, as ordinary hourly upserts
do. `just verify-bufo-publication TARGET RUN_ID` repeats this proof only while those
records still belong to that run; a later normal publication will correctly fail
the old-run timestamp check. Five rollup tests and 28 deployment-contract tests passed.

Runtime dependencies differ deliberately:

- HeavyPad installs the pinned `mps` package from this repo's `packages/mps`
  subdirectory with `uv --no-project`, avoiding the root project's dbt/atlas set.
- The existing managed image is Python 3.12, including its `3-latest` tag at test
  time. The whole `mps` distribution declares Python >=3.13. Cloud instead loads
  the specific shared modules from the same pinned checkout via a fixed,
  container-local `PYTHONPATH`, with explicit Prefect/httpx/pydantic/atproto/pdsx
  dependencies. The exact set imported the real flow in an isolated Python 3.12
  environment and then passed real Cloud reads and writes. This does not assert
  that every `mps` module supports Python 3.12.
- `LOGFIRE_READ_TOKEN` still works for original environment/plugin-based callers;
  the migrated task falls back to its runtime Secret block when the env var is
  absent. Neither deployment stores plaintext tokens in job variables.
- Table artifacts now use the awaited async creation API. The previous async
  flow did not await creation; its early dry-run completed without an artifact.
  The corrected runs persisted artifacts on both servers.

Two controlled Cloud bootstrap attempts were unsuccessful and are terminal:
`06adea77-a9a6-7685-8000-23e94806ee20` (Cancelled after infrastructure explicitly
reported exit code 2) and `06adea80-e1b9-7bb6-8000-8e96df46ed39` (Crashed, Python
version mismatch). A spaced Git requirement was split by the managed installer;
then the single-token form exposed the Python minimum. Neither reached flow
execution or PDS writes. The first attempt stayed Cancelling despite repeated
infrastructure-exited reports; its cancellation state was reconciled only after
that evidence. Startup failure cleanup is not assumed instantaneous in recovery.

The explicitly scoped command `just recovery-secret-blocks TARGET --block NAME`
checks canonical Secret values in memory; `--apply` repairs missing/drifted copies.
Targets are `heavypad` and `cloud`; permitted names are only
`logfire-read-token` and `operator-atproto-creds`. Matching values are not saved
again. The HeavyPad read-token copy was deliberately drifted while no candidate
runs were active; the normal check rejected it, canonical sync repaired it, and
the exact original bytes were verified restored. Existing credentials were not
rotated. The canonical store README records both consumers and issuer/consumer
rotation steps. The existing Cloud bot profile's own authentication was not migrated.

Before a real outage takeover, positively fence the normal owner, reconcile
in-flight writes and keep it stopped until the Cloud run is terminal. An
unreachable home API is not evidence that a flow process has stopped. If that
cannot be established, do not blindly run two publishers or claim automatic
failover is safe. This batch proves standalone remote execution with accessible
source, secrets and external data; it does not provide an automatic lease/fencing
service. The default two-day lookback also does not repair arbitrary older gaps;
Logfire windows are bounded and explicit backfill needs review before retention
expires.

To invoke the qualified writer manually only after fencing the normal owner:

```
just cloud-recovery-prefect --no-prompt deployment run bufo-traffic/bufo-traffic-recovery --params '{"config":{"dry_run":false,"days_back":2}}' --watch
```

Rollback first clears the HeavyPad schedule, cancels queued runs and waits for
active Cloud/HeavyPad writes to finish. Restore the original :07 source schedule,
register only that deployment, verify it is the sole recurring owner, and leave
Cloud manual-only. Source-only `schedules: []` is insufficient: explicitly clear
and verify the live schedule list. No cursor/volume/DuckDB state is needed for
this flow; output and source data already live off-host.

## Qualified public MCP health batch

`mcp-fleet-health` now runs hourly at :12 UTC on HeavyPad. Its original schedule
and three future jobs were cleared, and a database readback found no remaining
Scheduled/Pending/Running/Cancelling original runs before activation. Cloud is
manual-only with no notification automations or extra credentials.

| Resource | ID |
| --- | --- |
| Original deployment | `b356a5ff-8022-4b16-82e1-57e646623190` |
| HeavyPad deployment | `05b19ef0-225e-4a4e-96af-e28971d5f91a` |
| Cloud managed deployment | `fd786cae-fabd-448d-9bd0-f8a13c305f90` |
| HeavyPad successful run | `0becfef1-b95c-41ec-841f-046bd34014f7` |
| Cloud successful run | `06adeac2-ef79-7626-8000-fc28c57d460f` |

Both runs used source `ef71b407db77697a4c387c6272ce9500646db616` and reported
8/8 healthy public servers, exercising connection, protocol negotiation,
ClientGroup routing and tool discovery. The persisted markdown artifacts matched
exactly after excluding measured connection latency. The matching inventory was
Atmosphere MCP, Taproot, pdsx, plyrfm, pub-search, semble, streamplace-mcp and
tangled-mcp, with respective tool counts 44, 27, 9, 13, 7, 2, 2 and 26 at that
instant. No listed tools were invoked. This is a timed sample, not a guarantee
of future health or identical future results from different network locations.

Source `2e86006c62a30a46fe820c2d5aa8c1b832d95bae` activated the HeavyPad :12
schedule only after its old owner was drained and its notification rule was
installed. First automatic execution is queued for 02:12 UTC; that automatic
run has not yet been observed. Cloud has `schedules: []`. Concurrency is one and
the flow has a five-minute execution bound. Prefect 3.8.2 and FastMCP/fastmcp-slim
4.1.3 are pinned on both runtimes; the previous job upgraded FastMCP every run.
Future client upgrades are deliberate deployment changes, not silent drift.
The exact managed dependency set imported the real flow under Python 3.12 before
the real remote execution. Twenty-eight health semantics/Evergreen tests passed.

`just heavypad-prefect-monitoring` now verifies three rules: the existing failure
and sustained-burn rules plus `fleet unhealthy -> discord`. The latter matches
both fleet resource IDs, buckets by resource, and uses the existing copied
notification block. The previously approved single notification test qualifies
the common delivery transport. No additional synthetic message was sent for
this batch. Unhealthy-summary run links now use Prefect's runtime UI setting;
HeavyPad declares its private browser URL, while Cloud derives its own workspace
URL. Regression coverage checks the actual settings context rather than assuming
the original hostname everywhere.

The old automation description saying results live in Hub was stale: the current
`web/src` has no reader for these health keys/events. Its Prefect artifact reader
is the separate Gardener view, which remains on the original API. The deployed
Hub image `c344d5c` was also checked read-only: no fleet-health keys occur in its
server bundle, and its sole artifact-reader chunk maps to the Gardener route. Health reports
are saved Prefect artifacts with backend-correct run links. No Hub API/credential
retarget was made as part of this batch.

The broader `fleet-health` sweep has no Cloud counterpart: its
`pull-comment-bridge` check addresses `127.0.0.1:8791` on HeavyPad. Running that
unchanged on managed infrastructure would probe the wrong machine. A remote
counterpart needs explicit home-target reachability or an honest coverage model;
never remove that check and call the resulting sweep equivalent. Its schedule
moved to the HeavyPad server on the same host (see the fleet-health batch).

Rollback this batch by clearing HeavyPad's MCP schedule, cancelling queued runs
and draining active runs, then restore the original :12 schedule in source and
register just that deployment. Verify one schedule owner; keep the Cloud copy
manual-only and preserve artifacts/history. Keep the shared notification rules
while other migrated jobs still use them. Canonical DNS still resolves to the
original cloud node, and the Typeahead publisher retains its original cloud pool
and odd-day 06:00 UTC schedule.

## Qualified costs batch

- Original deployment: `140f6561-de9d-4c82-a230-24da3be1c291`.
- HeavyPad deployment: `8e8113ce-4d7a-4969-a7b4-08979bb9452c`.
- Cloud deployment: `e25512e9-5383-491e-947e-38254ec6d3ce`.
- Source exercised on both runtimes: `8a7284cc992ab3c89e7457de9e5c7652afab0cc3`.
- Read-only HeavyPad run: `97658650-f5ce-4c45-a2d0-80881190bd02`, $527.85.
- Read-only Cloud run: `06adeafa-6e87-7adb-8000-c946a316879f`, $527.84.
  Both returned the same 67 item identities and all five providers. The only
  amount difference was one cent on `typeahead-search-refresh:compute`, whose
  trailing-seven-day uptime estimate changes with collection time.
- Real Cloud publication: `06adeb10-4ce6-7e62-8000-f7247b38a131`, $527.81.
- Subsequent real HeavyPad publication: `f0550428-2daa-45a4-b648-99035ace9542`,
  $527.80. Both completed; public PDS readback after each matched every artifact
  line item, provider/project rollup and run generation timestamp. The latter
  remains the public `io.zzstoatzz.cost.snapshot/2026-10-26` record.

Each run's state type was `COMPLETED`, named `Incomplete`: existing Fly stopped
root-disk billing bytes are unavailable, and the existing Cloudflare token cannot
read R2 storage analytics. The observed stopped-machine count changed from 17
to 15 between samples. These are explicit measurement gaps, not missing provider
execution or a fully measured invoice. No token scopes or infrastructure changed.
Provider exceptions still retry and prevent publication after exhaustion; all
27 cost/Fly tests passed, including the real artifact path and refusal behavior.

Original source schedules were emptied in `f9b9d4db77974b512fb013190c3cac7ebc041b30`.
Explicit live schedule clear then removed the old daily schedule and its three
queued runs. Database readback found zero Scheduled/Pending/Running/Cancelling
old cost runs before either publication. The two new publishers ran sequentially
with no recurring cost owner during the qualification window.

HeavyPad alone now owns `0 8 * * *` UTC, default `config.dry_run=false`, from
`cd1ca07307ce1e714a5da57a99d06c2bd6b386e8`. Cloud has no schedule and defaults to
`config.dry_run=true`. The first automatic daily run is still future at this
readback; real manual publication above qualifies execution. Deployment IDs and
history were preserved. Source changes are on main and both remotes.

The four provider tokens are runtime Secret blocks copied from canonical SOPS:
`fly-api-token`, `cloudflare-api-token`, `neon-api-key`, `hetzner-tokens`.
HeavyPad uses canonical names; Cloud aliases them with `recovery-` to avoid
overwriting unrelated Cloud consumers. `COSTS_PROVIDER_BLOCK_PREFIX` selects
that namespace. The existing shared `operator-atproto-creds` handles publication.
Checks compare values without printing them. Controlled HeavyPad Fly-token drift
with no active cost job was detected, repaired byte-for-byte, and followed by a
no-op second apply. Issuer rotation remains manual/unperformed; update SOPS,
sync original and selected target consumers, then verify real collection and
publication before revoking the old credential. The secret-store README records
these consumer scopes and the rotation procedure.

```
just verify-cost-publication cloud CLOUD_RUN_ID
just verify-cost-publication heavypad HEAVYPAD_RUN_ID
just recovery-secret-blocks cloud --block fly-api-token
just recovery-secret-blocks heavypad --block fly-api-token
```

Read back Cloud publication before a subsequent publisher overwrites the same
UTC-day record. Before real recovery writes, fence the HeavyPad schedule and
process, reconcile any in-flight write, then explicitly pass `config.dry_run=false`.
An unreachable home API alone is not fencing. Roll back by draining/clearing the
HeavyPad cost owner first, restoring the old daily source schedule and registering
only that original deployment. Verify one owner; keep Cloud manual-only.

Encrypted PostgreSQL backup `20261026T014934Z.dump.age` was copied to the Mac's
`~/.local/state/prefect-home-backups/`; source and off-host SHA-256 matched.
It includes the new provider blocks and schedule state. Automatic encrypted
replication to R2 with a weekly restore check has since been enabled (see the
HeavyPad runbook); independent outage alerting remains a gate for critical migrations.

## Qualified fleet-health batch

`fleet-health` moved to HeavyPad on October 26, 2026. The original home-pool worker already
ran it on HeavyPad, so the runtime host and the `127.0.0.1:8791` pull-comment-bridge
vantage are unchanged; only schedule ownership moved. It needs only Prefect and its
httpx/pydantic dependencies, emits `fleet-health.unhealthy` (resource `fleet-health`)
and writes artifacts, with no Variables, blocks or cross-run state. HeavyPad's copied
`fleet unhealthy -> discord` rule already matched this resource; no test message was sent.

| Resource | ID |
| --- | --- |
| Original deployment | `fb012d28-b91e-48f9-af2a-e55fc2ce1b4d` |
| HeavyPad deployment | `c85b78c7-116f-4433-b001-d3bf47d9fefb` |
| HeavyPad manual run | `c955fa71-ad90-4ed1-abba-50b52b76b212` |
| First scheduled HeavyPad run | `6aa4e565-7ed8-41b9-8678-4491977f3e37` |

The manual run completed with the bridge check `ok` (HTTP 200). The original source
schedule was emptied in `0ea9aa4`; the live schedule was then cleared explicitly, and
readback found no Scheduled/Pending/Running/Cancelling original runs before the HeavyPad
schedule was activated from `c313bf8` (`3,18,33,48 * * * *` UTC, concurrency one,
`CANCEL_NEW`). The first scheduled run completed at 20:48:18 UTC in 22 seconds.
Its `auto_scheduled=true` record and artifact were read back: 63 targets, bridge
HTTP 200, and one unhealthy finding for `find-bufo/search` (HTTP 0). Completion
means the sweep ran; it does not assert every target was healthy. The three
copied notification rules and block hash were verified without a synthetic alert.

The R2 restore check now requires the HeavyPad fleet-health deployment ID.
Deployed script SHA-256 `4fb306697d9fd678eef1a7d4d83e112f9c05f16e24c0d58a6d366d4c6e0821c2`
matched source. Encrypted backup `20261026T215402Z.dump.age` uploaded to R2 and
restored in the isolated verifier at 21:54:10 UTC on October 26: nine deployments,
161 flow runs, nine blocks, three automations, and newest completion five minutes
before the dump. Ciphertext SHA-256:
`28fc1044676ec163915cb61623dbd24798996cd398531a0d8a223993b441e05f`.

The original catch-all `flow-run failure -> autofix` no longer sees fleet-health
failures, the same accepted gap as diagnostics and backlog. Rollback: clear the HeavyPad
schedule and cancel its queued runs, wait for an active run to finish, restore the
original schedule in `prefect.yaml`, register only that deployment, and verify one owner.

## Atlas and Bisk publication batch

MCP Atlas and Bisk snapshot publication transferred to HeavyPad on October 26.
The central source is my-prefect-server. The crawl moved from the Atlas product
repo to `flows/mcp_atlas/crawl.py`; both original and HeavyPad deployment targets
now point there. Atlas keeps its site, Worker, MCP server and record/layout
library, pinned at `238d50fc7b97fd57129f787f1be2460f3c1a1614`. Its old flow and
deployment file were removed in Atlas commit `73b7fe2`.

| Resource | MCP Atlas | Bisk snapshot |
| --- | --- | --- |
| Original deployment | `2e3b9588-630b-460d-b550-abcc2fe5b76c` | `d55978b9-a057-4aca-a8d3-38068249f592` |
| HeavyPad deployment | `32b67ef3-f639-46aa-8b93-2ab6e0c463f0` | `7f0c3fc7-cd6f-4a22-bf83-fb6e606a624d` |
| Completed manual run | `2e2ccc44-2db0-4f9f-a827-36fe58bfb49e` | `f93f797c-3082-43b6-b397-574f4a74eeec` |
| First queued automatic run | `6471ce1e-ddb2-4ee2-9b56-fe682f74629e` | `f95d0b19-8fd4-41e5-a39e-a01dbd7aa59f` |
| HeavyPad cron in UTC | `45 */6 * * *` | `*/10 * * * *` |

Both old live schedules were explicitly cleared before HeavyPad publication
tests. Readback found no Scheduled/Pending/Running/Cancelling old runs. The new
schedules were activated from `617b0a4`, with one deployment slot and CANCEL_NEW.
Automatic runs were read back with `auto_scheduled=true`: Bisk at 00:00 UTC and
Atlas at 00:45 UTC on October 27. Bisk's first automatic run completed at
00:00:20 UTC, starting at 00:00:12. Atlas's first automatic run remains queued;
its manual publication is verified, but the six-hour cadence has not yet fired.

The Atlas run completed at 23:49:58 UTC and published 13 servers from seven DIDs,
11 live remotes. Public JSON readback returned 13 servers. Bisk's successful
run completed at 23:57:01 UTC, publishing 330 KB to `bisk/bisk.json`; public R2
readback showed `generatedAt=2026-10-26T23:56:48.123283+00:00`, 634 pool members,
673 live entries and 144 crownings.

The first Bisk attempts failed before writing because its Secret-block sentinel
environment variables were not resolved. Prefect 3.8.2's process Runner merges
worker settings after the deployment environment, overwriting
`PREFECT_PLUGINS_ENABLED=true` with the worker's explicit false value. Merely
restoring the original package or switching to the engine module did not fix
that precedence. The final command starts with
`env PREFECT_PLUGINS_ENABLED=true PREFECT_PLUGINS_ALLOW=mps-secrets`, after the
worker merge, and preserves the root package that registers the plugin. Its real
publication passed. No credential values entered deployment metadata.

The four selected blocks match the canonical SOPS entries and original server.
The new Atlas token consumer passed an actual drift-detection/repair check, and
repeated apply was a no-op. Issuer credentials were not rotated.

All three copied notification rules and the Discord block hash matched. The
failure rule points run links at HeavyPad. Bisk's first failure event
`1186695c-944c-4324-8c3b-f71c27cc75e8` led to successful send-notification audit
event `8d1a89f4-60f0-4764-8676-bbcd2f6c1bd0` at 23:51:00 UTC. No synthetic Discord
test was sent. Automatic diagnosis still remains on the original server and
must move with the Gardener chain.

Both new deployment IDs are required by the R2 restore checker. The six prepared
Phi schedules stay inactive and their original schedules remain unchanged.
Encrypted backup `20261027T000008Z.dump.age` uploaded and passed the isolated R2
restore verifier at 00:00:16 UTC: 11 deployments, 196 runs, 13 blocks, three
automations, and the newest completed run within a minute of the dump. Its
ciphertext SHA-256 is
`51d360ae69372e54b517a2e471fbee9e85990d057d9ea5a560ed17b6c824199f`.
The restore checker includes both new deployment IDs and source/deployed script
hashes are checked during this rollout. Local validation passed 822 tests with
seven skipped, Ruff, type checking and deployment contracts; CI runs separately.

Rollback: clear the affected HeavyPad schedule, cancel its unstarted queued runs
and wait for active publication to finish. Restore only that original schedule
in central `prefect.yaml`, land it, and register only the selected original
deployment. Its central source path and original Secret blocks remain usable.
Confirm one scheduled owner before resuming publication. Do not restore the
Atlas product's removed deployment file as a second owner.

After all remaining deployments and clients are migrated and Hetzner is retired,
review logical groupings for the central flows directory with Nate before a
broader folder reorganization.

HeavyPad disk warnings, critical capacity/inode warnings and missing-sample
alerts are now centralized in Logfire, with Grafana remaining dashboard-only.
See [monitoring](../monitoring.md#heavypad-storage-alerts). Include the bandwidth
watchdog's original-server startup Secret lookup and event/heartbeat delivery in
the remaining client migration inventory. Before replacing Logfire, run a bounded
ingest/storage/query comparison against actual volume and retention requirements;
keep the current service while evaluating a separate Zig/OTel prototype.

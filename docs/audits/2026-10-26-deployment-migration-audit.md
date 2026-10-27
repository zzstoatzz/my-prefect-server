# Deployment audit before HeavyPad migration

Migration update: MCP Atlas and Bisk Snapshot now have active HeavyPad schedules
and empty original schedules. Bisk's first automatic run completed October 27 at
00:00:20 UTC. Atlas passed real manual publication and its first automatic run
is queued for 00:45 UTC. The detailed inventory and schedule measurements below
describe the earlier audit snapshot; the [migration receipt](../migrations/heavypad-cloud-recovery.md#atlas-and-bisk-publication-batch)
records the resulting ownership changes. Six Phi clock definitions are prepared
inactive; their original schedules still own execution.

Checked October 26, 2026 around 22:55 UTC. Keep the active workloads and their
dependencies, retain the six migrated Hetzner records for history, and omit
`pds-records`, `dep-bump`, and `pi-pr` from the migration. Nate approved skipping
all three and retiring them with Hetzner during the walk. No deployment,
schedule, automation or routing was changed by this audit.

## Migration state

The original API at `https://prefect-server.waow.tech/api/` reports version 3.8.2
and 46 deployments. Its six migrated deployments have empty live schedule lists.
HeavyPad has nine deployments: those six, plus `evergreen-atlas`,
`backup-freshness`, and the manual canary. Eight HeavyPad deployments have active
schedules. Evergreen owns its own deployment registration.

HeavyPad's API, services, PostgreSQL and Redis pods are all Running. Its separate
`prefect-heavypad-worker.service` is enabled and running. Docker is enabled at
boot; both k3d containers have been up for 15 hours. The enabled
`prefect-home-k3s-guard.service` has Result=success and ExecMainStatus=0. It is a
completed boot guard, so inactive/dead is its expected steady state. Recent
HeavyPad fleet-health, diagnostics, backlog, MCP health and Bufo runs completed.

The server runs in k3d under Docker; it is not a standalone systemd Prefect API
process. The systemd worker and boot guard already exist. Canonical routing still
targets the original control plane.

## Decisions before the next batch

| Deployment | Purpose | Recommendation |
| --- | --- | --- |
| `pds-records` | Manual PDS record listing, creation, updates and deletion | Approved: do not migrate; retire with Hetzner. No observed runs, and pdsx exposes the same operations. |
| `dep-bump` | Update dependencies across downstream repositories, test and propose changes | Approved: do not migrate; retire with Hetzner. No observed runs; source describes it as ad hoc. |
| `pi-pr` | Have isolated Pi execute a task and propose a Tangled pull as Gardener | Approved: do not migrate; retire with Hetzner. No observed runs; lack of a schedule is intentional. |

Do not treat the other zero-run deployments as unused: enabled automations target
`autofix-revise` and `phi-pull-review`, while `phi-character-retro` is scheduled on
the first of the month. Keep those dependencies intact while auditing the
Gardener and Phi workflows.

An existing disabled `fastmcp-triage__automation_1` points at a deployment absent
from the current inventory. Leave it disabled and omit it from a fresh migration
unless the triage workflow is deliberately restored.

## Complete original deployment inventory

Completed counts are current Completed rows in the retained database whose
expected start falls within the last 30 days and before the observation time.
They are not lifetime counts, proof of useful output, or evidence that every day
of the window is retained. Zero means no matching run rows for the six zero-run
candidates, rather than merely no successful runs. Event-driven and manual
deployments can be needed without a recurring schedule.

| Deployment | Completed in window | Disposition and dependency |
| --- | ---: | --- |
| `autofix` | 39 | Keep with Gardener; failure automation targets it. |
| `autofix-revise` | 0 | Keep with Gardener; enabled revision automation targets it. |
| `bisk-snapshot` | 2018 | Keep; active publication tied to round/day boundaries. |
| `brief` | 333 | Keep with analytics pipeline and completion triggers. |
| `bufo-traffic` | 315 | Already migrated; preserve original history, schedule empty. |
| `classify-emails` | 334 | Keep with analytics pipeline and shared DuckDB writers. |
| `costs` | 21 | Already migrated; preserve original history, schedule empty. |
| `dep-bump` | 0 | Approved to retire with Hetzner; do not migrate. |
| `diagnostics` | 322 | Already migrated; preserve original history, schedule empty. |
| `docket` | 14 | Keep; completion-triggered publication. |
| `email-backlog` | 26 | Keep pending email workflow decision; manual use observed. |
| `email-triage` | 9 | Keep; daily schedule and an existing paused approval. |
| `fastmcp-backlog` | 440 | Already migrated; preserve original history, schedule empty. |
| `fastmcp-brief` | 181 | Keep with watch-fastmcp, variables and event automations. |
| `fleet-health` | 1334 | Already migrated; preserve original history, schedule empty. |
| `ingest` | 336 | Keep with analytics pipeline, local services and shared lock. |
| `investigate` | 4 | Keep with Gardener; recent manual execution observed. |
| `leaflet-atlas` | 56 | Keep; candidate for a separately qualified migration. |
| `mcp-atlas` | 56 | Keep; external source resolved below. |
| `mcp-fleet-health` | 316 | Already migrated; preserve original history, schedule empty. |
| `merge-approved` | 1 | Keep with Gardener; approval chain targets it. |
| `pds-records` | 0 | Approved to retire with Hetzner; do not migrate. |
| `phi-atlas` | 14 | Keep; daily memory/atlas publication. |
| `phi-character-retro` | 0 | Keep pending product decision; monthly first-of-month schedule. |
| `phi-chicken-precheck` | 14 | Keep pending product decision; time-sensitive bot pass. |
| `phi-chicken-scout` | 14 | Keep pending product decision; time-sensitive bot pass. |
| `phi-curation` | 2 | Keep pending product decision; weekly bot pass. |
| `phi-editorial` | 14 | Keep pending product decision; daily bot pass. |
| `phi-likes-review` | 2 | Keep pending product decision; weekly bot pass. |
| `phi-memory-synthesis` | 323 | Keep with analytics pipeline and completion triggers. |
| `phi-pull-review` | 0 | Keep with Gardener; enabled proposal-review automation targets it. |
| `phi-tag-maintenance` | 14 | Keep; daily memory maintenance. |
| `phone-presence` | 49 | Keep; recent phone calls to the flow; local lighting and presence state. |
| `pi-agent` | 5 | Keep pending runner consolidation; recent use, distinct home runtime. |
| `pi-pr` | 0 | Approved to retire with Hetzner; do not migrate. |
| `pub-search-snapshot` | 167 | Keep; next migration candidate after active run and state checks. |
| `strata-hourly` | 337 | Keep; archive checkpoint and retained segment dependencies. |
| `stream-admission` | 7 | Keep; external native build gate, no recurring schedule needed. |
| `test-pull-patch` | 2 | Keep with Gardener; recent execution observed. |
| `transform` | 333 | Keep with analytics cohort and Dagster execution. |
| `typeahead-enrich-backfill` | 14 | Keep; active enrichment, budget and Turso limits. |
| `typeahead-identity-hourly` | 337 | Keep; active identity/profile freshness. |
| `typeahead-index` | 7 | Keep; separate cloud builder, storage and publication qualification. |
| `typeahead-plc-identity` | 1 | Keep for now; live schedule disabled, successful October 19 run. Clarify future cadence. |
| `watch-fastmcp` | 4037 | Keep with briefing cohort; event production and dedup variables. |
| `watch-tangled-pulls` | 338 | Keep with Gardener; comment polling and dedup checkpoint. |

## External atlas source

The older migration inventory marked `mcp-atlas` unqualified because
`flows/crawl.py` is absent from this repository. Its live pull steps instead
clone `zzstoatzz.io/mcp-atlas`, where that file and the operational documentation
exist. The deployment completed at 18:45 UTC today and has 56 Completed rows in
the audit window.

The authoritative relay sweep reconciles deletions and long-tail records. A
separate Cloudflare Worker fast path refreshes every five minutes, so these two
paths have different responsibilities. Do not remove the Prefect crawl merely
because the Worker also refreshes data. Before moving it, pin its external source
and verify the ingest credential, full-sweep failure behavior and publication
against the same output destination. Existing pull steps track main.

## Migration order and outstanding state

The active-schedule assessment below revises the earlier order: qualify
`mcp-atlas` and `bisk-snapshot` first. Before moving `pub-search-snapshot` or
`leaflet-atlas`, isolate long builds from the monitoring worker. Preserve the
atlas history SQLite file and qualify actual outputs.
There was a running pub-search snapshot during this audit; reconcile that run
before transferring ownership. Keep the shared DuckDB writers together: limits
in separate Prefect instances do not coordinate access to the same file.

The original email-triage run `aa1e067f-2127-4575-a412-fa8b0f56092c` is Paused.
Resolve or explicitly reconcile its approval on the original server rather than
recreating the request on HeavyPad. No approval was resumed by this audit.

The live original automation inventory has 28 records, 27 enabled. Migrating
deployment definitions alone would leave event chains, notification blocks,
variables, callers and run IDs behind. Keep Hetzner until those dependencies,
the Typeahead builder placement, ingress and routing rollback are accounted for.

## Active schedule assessment

The original control plane has 21 deployments with active recurring schedules.
Twenty use `home-pool`, whose execution already takes place on HeavyPad; moving
them changes orchestration ownership. `typeahead-index` uses its separate cloud
pool. Cadences below reflect the live schedules; clock times are UTC.

The new HeavyPad process pool has a concurrency limit of one, and its worker also
starts with `--limit 1`. The worker's whole systemd service is capped at 2 GiB and
one CPU. Do not assume the existing heavier home jobs fit this worker merely
because they already execute on the same physical host. Separate capacity needs
its own worker allowance and resource budget; another queue alone cannot bypass
the pool and worker limits.

Runtime estimates below are mean elapsed times of Completed runs started within
the last 14 days. They exclude failed runs, queue time and cold-start
qualification, and do not establish output correctness.

| Deployment | Active cadence | Assessment |
| --- | --- | --- |
| `mcp-atlas` | Every six hours at :45 | First candidate. Mean 10 s, p95 22 s over 56 completions. External source, ingest Secret block and authoritative publication need qualification. |
| `bisk-snapshot` | Every ten minutes | First batch candidate. Mean 10 s, p95 15 s over 2,013 completions. Bucket-scoped R2 credentials, round boundaries and single publisher ownership. |
| `pub-search-snapshot` | Every two hours at :40 | Keep, but use separate execution capacity. Mean 19 min, p95 22 min over 167 completions. Shared persistent builder root and production pointer writes require fencing old execution. |
| `leaflet-atlas` | Every six hours at :00 | Keep, but use separate execution capacity. Mean 35 min, p95 39 min over 56 completions. Python 3.13, ML dependencies, summaries, Pages publishing and persistent atlas history. |
| `phi-curation` | Monday 03:00 | Small clock-transfer candidate with other Phi triggers; mean 1 s over two completions. Verify bot acceptance and downstream effect separately. |
| `phi-likes-review` | Sunday 20:00 | Small clock-transfer candidate; mean 1 s over two completions. Same control-token and bot dependency. |
| `phi-editorial` | Daily 15:00 | Small clock-transfer candidate; mean 1 s over 14 completions. Same control-token and bot dependency. |
| `phi-character-retro` | First of month 17:00 | Same clock-transfer cohort, but no matching successful run in the runtime sample. Qualify before enabling. |
| `phi-chicken-scout` | Daily 18:00 | Small clock-transfer candidate; mean 2 s over 14 completions. Preserve market timing; do not replay missed deadlines. |
| `phi-chicken-precheck` | Daily 04:00 | Small clock-transfer candidate; mean 1 s over 14 completions. Preserve market timing; do not replay missed deadlines. |
| `phi-tag-maintenance` | Daily 13:00 | Separate from the HTTP clock cohort. Mean 46 s; memory writes and provider dependencies. |
| `phi-atlas` | Daily 13:00 | Separate from the HTTP clock cohort. Mean 7 min; shared archives, ML and publication dependencies. |
| `strata-hourly` | Hourly at :21 | Later stateful candidate. Mean 4 min, p95 8 min; preserve SQLite checkpoint, filesystem lock and retained segments. |
| `watch-fastmcp` | Every five minutes | Move with briefing/event cohort despite mean 2 s; variables and emitted events must follow ownership. |
| `fastmcp-brief` | Every four hours | Move with watch-fastmcp and event automations; mean 10 s does not make cursor/event state disposable. |
| `watch-tangled-pulls` | Hourly at :17 | Move with Gardener and its comment dedup variable, target deployment IDs and approval chain. Mean 2 s. |
| `ingest` | Hourly at :00 | Move with shared analytics writers and their completion triggers, not independently. Mean 65 s. |
| `email-triage` | Daily 13:20 | Defer until paused approval is reconciled and local mailbox/result state follows. Approximately 12-hour elapsed completions include approval waiting, not continuous CPU work. |
| `typeahead-identity-hourly` | Hourly at :20 | Later Typeahead cohort; mean 3 min, p95 8 min, shared Turso writes and ingestion pacing. |
| `typeahead-enrich-backfill` | Daily 20:00 | Dedicated capacity required. Mean 2.9 hours, p95 3.5 hours; preserve budgets and publication-service headroom. |
| `typeahead-index` | 06:00 on matching days of month for `*/2` | Separate cloud-builder migration. Preserve its current cron semantics, builder/runtime storage and accepted-generation checks. This cron is not a strict elapsed 48-hour interval. |

The six short Phi flows call the bot's control API and return after acceptance.
Their short Prefect duration does not measure the bot pass's duration or prove
that its work completed. Bisk and the MCP crawl publish real output; the first
batch still requires a controlled execution/readback, old-owner fencing, and a
genuine scheduled run before declaring migration complete.

## Evidence

- Original conversation: New Realtime Voice Chat, October 24,
  `01a16fd7-8019-70c3-8516-beb49c421f30`, in
  `/Users/nate/Documents/Codex/2026-10-24/new-realtime-voice-chat-3`.
  Its `prefect-server-handoff.md` summarizes the first migration work.
- Later conversation: Continue Prefect migration handoff,
  `01a179ef-9d8f-7bc3-bcbb-75d0290c5f42`, includes the fleet-health continuation.
- Live self-hosted Prefect MCP identity, deployments, automations and flow runs;
  original PostgreSQL aggregate in a read-only transaction with a 15-second
  statement timeout; HeavyPad CLI deployment/run reads; SSH service checks;
  explicit HeavyPad Kubernetes target.
- [Migration and recovery plan](../migrations/heavypad-cloud-recovery.md),
  [HeavyPad operations](../../deploy/heavypad-prefect/README.md),
  original and HeavyPad deployment files, and `deploy/automations.yaml`.
- External source: `~/tangled.org/zzstoatzz.io/mcp-atlas`,
  `flows/crawl.py` and `docs/operations.md`.

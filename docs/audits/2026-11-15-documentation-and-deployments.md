# documentation and deployment audit — November 15, 2026

> This audit records the state before the subsequent repository cleanup. The
> cleanup removes retired source/configuration and reconciles current guidance;
> live deployment and automation decisions below remain separate.

The README needs a small navigation update. The operating documentation needs a
focused reconciliation with the October home cutover and November changes. Keep
the README concise; the main problem is contradictory runbooks and plugin skills,
not a missing deployment catalog in the landing page.

Production has **52 deployments: 34 scheduled and 18 unscheduled**. All 34
scheduled deployments have retained run activity; ten unscheduled deployments
also do. Eight unscheduled deployments have no retained runs. That is not enough
to call all eight unused: several belong to the deliberately suspended autofix
chain, and others are manual utilities or probes. No deployment was changed,
paused, resumed, triggered, or deleted during this audit.

## scope and evidence

- Repository revision: `0babee9d544785149d65e12d51f4a7fb9d8ffd29` on `main`.
- Git window: October 15, 2026 00:00 America/Chicago through the audit. Reviewed
  the history inventory across all local refs: 605 recent commits (559 on main),
  and project history from the March 17 initial commit, 1,300 commits across all
  refs (1,249 on main). Commit subjects, changed paths and targeted diffs informed
  the assessment; this is not a line-by-line review of every historical patch.
- Documentation: inventoried and structurally scanned all 109 tracked Markdown,
  reStructuredText and text documents, 13,437 lines. Reviewed active operating
  guidance in detail and relevant historical receipts against source/history.
  The inventory distinguishes 45 documents outside historical directories from
  64 under archives, audits, incidents, migrations and spikes. Classification by
  path does not itself establish that a document is current.
- Production snapshot began **2026-11-15 08:03 UTC** (02:03 CST); Cloud was queried
  separately shortly afterward. These are sequential reads, not a database-wide
  atomic snapshot. See the [machine-readable evidence](2026-11-15-documentation-and-deployments.json)
  for exact timestamps, deployment IDs, last completed-type runs, schedules,
  pools, automation targets, source ownership, and all 109 documentation paths.
- Queried all 52 self-hosted deployments, six pools and 34 automations, then all
  50 deployments and seven automations in the configured Prefect Cloud bot
  workspace. Checked live home registration/worker state and the production
  retention configuration. Historical deleted deployments cannot be inventoried
  from the current API; relevant removals were checked in Git.
- **Production retains terminal root flow runs for 14 days**, verified from the
  live vacuum setting (`1209600` seconds) and the Zig vacuum implementation.
  The October 15-to-now query therefore undercounts a full month. In the table,
  “retained” means starts still present in that query, approximately November
  1–15, not a complete monthly total. No backup restore was performed.
- Seven-day counts use the snapshot time minus seven days. Counts include manual,
  event, recovery and scheduled starts. A non-null start time may also describe
  a startup crash; it does not prove the flow body executed. Future scheduled
  runs are excluded. Zero means no retained run, not “never used.” Cloud counts
  use the October 15 cutoff, without assuming production's retention policy.
- Completed state **type** does not mean full business success: `Incomplete`,
  `Degraded`, `Unanswered`, `Skipped` and validation-only outcomes need their own
  interpretation. Phi trigger completion confirms the request succeeded, not
  that Phi finished its downstream pass. Run frequency also does not prove that
  a person reads an output or that a deployment is worth its cost.

## what the history explains

| Period | Change | Documentation implication |
| --- | --- | --- |
| March 17 onward | Initial Hetzner k3s, Python Prefect/Helm, Kubernetes worker; hub ingestion and transform chains followed. | Early setup and Helm instructions are historical, not current production entrypoints. |
| June | Zig control plane; home execution; hub edge and data sync split. | Several top-level guides still describe this intermediate architecture. |
| August–September | Runtime Secret resolution, immutable job sources/wheels, Dagster asset ownership. | Deploy-time secret baking, always-pull-main and Prefect-owned dbt instructions are stale. |
| October 15–24 | Gardener/runtime changes and laptop triage retirement. | Laptop and Sprites operating instructions need retirement labels or replacement. |
| October 25–27 | Home control-plane cutover, explicitly targeted CLI wrappers, trusted-host registration, Hetzner retirement. | This is the largest source of contradictory runbooks. The README was deliberately shortened October 27 (`b860a11`). |
| November 1–8 | Hub restored after mistaken removal, analytics maintenance/snapshots, autofix suspension, prefect-dr backups. | Preserve consumer checks; distinguish paused chains from unused services; remove superseded backup installation steps. |
| November 11–15 | Local lighting time zones, presence/relay/status work, Typeahead and builder removals, pub-search rename and new runtime pins. | Schedule, ownership, inventory and runtime examples need targeted refreshes. |

Useful cutover anchors include `1506d7a` (trusted-host registration), `90bad64`
(target wrappers), and `d7a3ee3` (Hetzner retirement). November anchors include
`0559c53` (hub restoration), `8e96f50`/`fa07ddd` (autofix suspension), and
`c1c80a2`/`d0977c2` (recent removals). An earlier deployment audit exists on an
unmerged ref at `00f38e05978c9fef10a281203648097b4530655f`; it is corroborating
history, not current operating policy or a file to restore without review.

## documentation changes recommended

Priority 1 means an operator or agent could follow stale instructions against
the wrong target or restore retired behavior. Priority 2 means the description
misstates current ownership, cadence or service behavior. Priority 3 is navigation
or historical organization. This report assesses changes; it does not silently
replace operational instructions before the related decisions are made.

| Priority | Documents | Finding and recommended change |
| --- | --- | --- |
| 1 | [home stack runbook](../../deploy/heavypad-prefect/README.md) | The top retirement notice and later current registration section conflict with the body: required/billed Hetzner, future canonical cutover and pilot cohorts. Separate current operation/recovery from dated migration narrative. Backup prose acknowledges prefect-dr but still instructs copying removed `offsite.sh`/`offsite-verify.sh`; replace that procedure with the installed prefect-dr contract. |
| 1 | [operations](../operations.md), [deployment validation](../deployments-validation.md) | Legacy-manifest/default deploy examples, manual-only registration and CI registration claims conflict with the live trusted-host timer. Use explicit target/config guidance and distinguish CI validation from host registration. `just automations`, `just coding-jobs` and parts of home status tooling still use legacy environment assumptions; repair or clearly scope those recipes before recommending them as current. |
| 1 | [deployment skill](../../plugins/mps/skills/deployments/SKILL.md), [server access skill](../../plugins/mps/skills/server-access/SKILL.md) | They teach legacy `prefect.yaml`, deploy-all, always-pull-main, deploy-time secret resolution, old auth/context and worker assumptions. Align with current target wrappers, `prefect.heavypad.yaml`, runtime secrets, declared Git/wheel delivery and trusted-host registration. Link canonical procedures instead of duplicating them. |
| 1 | [home worker skill](../../plugins/mps/skills/home-pool-worker/SKILL.md), [ad hoc skill](../../plugins/mps/skills/adhoc-runs/SKILL.md), [cost skill](../../plugins/mps/skills/infra-costs/SKILL.md) | Old single-worker/home-pool limits, server placement, source delivery and Hetzner inventory are unsafe operational shortcuts. Use current pool ownership and units; explain local versus production execution and current cost coverage/declared ownership. |
| 1 | [Albedo sandbox](../../deploy/albedo-sandbox/README.md), [legacy manifest](https://github.com/zzstoatzz/my-prefect-server/blob/0babee9d544785149d65e12d51f4a7fb9d8ffd29/prefect.yaml), [repository instructions](../../AGENTS.md) | Weekly Albedo drift auditing is advertised and its legacy schedule is active, but no matching production deployment exists. Decide whether to register it through the current manifest or retire that claim; do not activate it merely to reconcile docs. Until repaired, “inactive rollback definitions” has this exception. Update legacy target wording after completed retirement. |
| 2 | [hub](../hub.md), [Dagster](../../deploy/dagster/README.md), [home worker](https://github.com/zzstoatzz/my-prefect-server/blob/0babee9d544785149d65e12d51f4a7fb9d8ffd29/deploy/home-worker/README.md), [hub sync](https://github.com/zzstoatzz/my-prefect-server/blob/0babee9d544785149d65e12d51f4a7fb9d8ffd29/deploy/hub-data-sync/README.md) | Home-pool, Hetzner edge/rsync, laptop triage, pds-records and CI deployment descriptions mix retired and current arrangements. Preserve the ingestion→Dagster→consumer design; document the actual split pools and current source paths. Diagnostics is hourly at :37, pub-search-snapshot daily at 06:40 UTC. Fixed UTC hours are not year-round Central wall-clock times. |
| 2 | [agent tooling](../agent-tooling.md), [coding jobs](../coding-jobs.md), [Gardener registry](../../deploy/gardener/REGISTRY.md), [autofix](../autofix.md), [pull bridge](../../deploy/pull-comment-bridge/README.md) | Reconcile old Sprites/pi-pr examples with current runtime ownership; make the deliberately paused autofix/revise/review chain and disabled exe worker explicit wherever an active path is described. Keep immutable artifact and rollback contracts. The pull bridge can emit/reconcile events without a live revise consumer. |
| 2 | [FastMCP agentic workflows](../agentic-fastmcp.md) and associated [experiment files](../../deploy/agentic-workflows/) | Documented Cloud validation/publication deployment IDs no longer exist. Current active Cloud triage/verification pipelines pull from PrefectHQ/fastmcp, and canary pipelines from prefectlabs/fastmcp-canary. Archive the original experiment and point to the current owners; do not redeploy old IDs as a documentation fix. |
| 2 | [FastMCP attention](../fastmcp-attention.md) | The retirement note conflicts with later live laptop instructions. Describe current warehouse/backlog/brief roles and disabled brief event triggers. Date historical run-volume/cost estimates instead of presenting them as current. |
| 2 | [presence and lighting](../presence-lighting.md) | Recent ownership text is useful, but the deployment section still describes a pending private bundle, old source variables/manifest and ingress setup. Reconcile with current `presence-deploy`, the home manifest and edge configuration. |
| 2 | [outage runbook](../heavypad-outage-runbook.md), [monitoring](../monitoring.md) | Reconcile five active process workers plus the disabled exe worker, and four Cloud recovery deployments versus the three documented qualified paths. Keep cost recovery's incomplete-coverage limitation explicit. Distinguish current home monitoring from legacy Helm/retention instructions and date old inventories. Preserve intentional restore rehearsal/rollback receipts. |
| 2 | [alert dispatch](../alert-dispatch.md) | Distinguish the proposed automatic incident workflow from the implemented manual Albedo pilot; four manual checks do not establish an automated dispatch loop. |
| 2 | [costs](../costs.md), [cost declaration](../cost-declaration.md), [prompt caching](../prompt-caching.md) | Declared ownership reconciliation already exists, despite future-tense language. Preserve known coverage gaps. Date old model, price and hit-rate measurements; model examples drift from current 5.5 use. Do not substitute guessed current prices. Repair the imported missing source link. |
| 3 | [README](../../README.md) | Keep purpose/setup/use/design concise. Add direct links to the current home runbook, deployment validation, analytics and outage guidance; briefly identify the authoritative home manifest. Python >=3.13 remains the support floor; a pinned development interpreter is a separate fact. Do not add a static deployment inventory here. |
| 3 | [Redis durability](../archive/redis-durability.md), [tailnet migration](../archive/tailnet-only.md), [policy draft](../archive/tailnet-policy-draft.md) | Mark or move dated Hetzner-era migration/proposal material; the unapplied draft and completed migration should not compete as current policy. Preserve historical evidence. |

No substantive correction was identified in the reviewed contracts in
[analytics](../analytics.md), [email backlog](../email-backlog.md),
[lighting](../lighting.md), [Prefect patterns](../prefect-patterns.md),
[service retirement](../service-retirement.md), or
[stream activity watch](../stream-activity-watch.md). This is not a guarantee that
every external fact in those documents was independently revalidated. Recent
phone, alerts MCP, Inbox Zero and Proton Bridge guidance is largely consistent
with the reviewed source. Keep historical audits/incidents as dated evidence;
fix links and routing rather than rewriting their past observations.

Four broken inline relative file links were found. External URLs, anchors and
plain-text paths were not exhaustively link-checked:

| File | Broken target | Action |
| --- | --- | --- |
| `docs/cost-declaration.md:8` | `../../sources/infra-cost-home-box-2026-06.md` | Replace with a durable available source or remove the unavailable citation. |
| `docs/fastmcp-attention.md:138` | `../deploy/laptop-worker/README.md` | Replace with a retirement receipt/history reference. |
| `docs/hub.md:160` | `phi-atlas.md` | Point to `archive/phi-atlas.md`, explicitly historical. |
| `docs/spikes/cached-prefect-pull-step.md:171` | `../audits/2026-09-28-home-bandwidth.md` | Recover the intended historical reference or remove the dangling link. |

## production deployment inventory

All 51 entries in `prefect.heavypad.yaml` exist live. The extra live deployment,
`evergreen-timeline`, pulls from the Evergreen repository; it is active and has
separate source ownership. Its absence from this repository's manifest is not
orphan evidence. The live registration receipt matches the audited main revision.

Cron expressions below are the current schedule, not necessarily the cadence for
the entire count window. UTC is explicit except for lighting (America/Chicago)
and two schedules with no explicit timezone (shown as default TZ). “Last start /
state” uses UTC and the latest retained run's state at inspection. The linked
JSON includes pool and last completed-type run for every deployment. A dash or
zero is not an assertion about pre-retention history.

| Deployment | Schedule or caller | Starts, 7d | Starts, retained | Last start UTC / state | Assessment |
| --- | --- | ---: | ---: | --- | --- |
| `albedo-alert-check/albedo-alert-check` | manual | 4 | 4 | 2026-11-13 07:45 / NothingNeeded | Keep: manual pilot exercised four times. |
| `analytics-maintenance/analytics-maintenance` | `37 9 * * *` UTC | 7 | 14 | 2026-11-14 09:37 / Crashed | Keep; investigate startup failure (see below). |
| `atproto-docs/atproto-docs` | `27 */2 * * *` UTC | 84 | 169 | 2026-11-15 06:26 / Completed | Active; retain. |
| `autofix-revise/autofix-revise` | event disabled; paused | 0 | 0 | none retained | Intentionally suspended; decide with autofix chain. |
| `autofix/autofix` | event disabled; paused | 0 | 51 | 2026-11-06 07:25 / Skipped | Intentionally paused Nov 6; do not resume. |
| `backup-freshness/backup-freshness` | `5 7 * * *` UTC | 7 | 15 | 2026-11-15 07:05 / Completed | Active; retain. |
| `bisk-snapshot/bisk-snapshot` | `*/10 * * * *` UTC | 1009 | 2018 | 2026-11-15 08:00 / Completed | Active; retain. |
| `brief/brief` | transform completed | 161 | 314 | 2026-11-15 08:02 / Completed | Active transform consumer; remove stale automation only. |
| `bufo-traffic/bufo-traffic` | `7 * * * *` UTC | 171 | 340 | 2026-11-15 07:06 / Completed | Active; retain. |
| `classify-emails/classify-emails` | ingest completed | 161 | 325 | 2026-11-15 08:00 / Completed | Active ingest consumer. |
| `costs/costs` | `0 8 * * *` UTC | 14 | 22 | 2026-11-15 08:00 / Incomplete | Active; all retained outcomes Incomplete, coverage caveat. |
| `diagnostics/diagnostics` | `37 * * * *` UTC | 172 | 341 | 2026-11-15 07:37 / Completed | Active; retain. |
| `docket/docket` | phi-atlas completed | 6 | 13 | 2026-11-14 13:09 / Completed | Active phi-atlas consumer. |
| `email-backlog/email-backlog` | manual | 0 | 0 | none retained | Keep on demand; zero recent demand does not obsolete a utility. |
| `email-triage/email-triage` | `20 13 * * *` UTC | 7 | 15 | 2026-11-14 13:20 / Unanswered | Review usefulness/delivery: ten Unanswered outcomes. |
| `evergreen-timeline/evergreen-timeline` | `17 */3 * * *` UTC | 58 | 115 | 2026-11-15 06:17 / Completed | Active; owned by Evergreen repository. |
| `fastmcp-backlog/fastmcp-backlog` | `*/30 * * * *` UTC | 336 | 673 | 2026-11-15 08:01 / Completed | Active; retain. |
| `fastmcp-brief/fastmcp-brief` | `0 */4 * * *` UTC | 122 | 237 | 2026-11-15 08:02 / Completed | Active schedule; both event triggers currently disabled. |
| `fastmcp-warehouse/fastmcp-warehouse` | `45 * * * *` UTC | 168 | 326 | 2026-11-15 07:44 / Completed | Active; retain. |
| `fleet-health/fleet-health` | `3,18,33,48 * * * *` UTC | 674 | 1349 | 2026-11-15 08:03 / Completed | Active; retain. |
| `ingest-segment-collections/strata-hourly` | `21 * * * *` UTC | 168 | 338 | 2026-11-15 07:20 / Completed | Active; retain. |
| `ingest/ingest` | `0 * * * *` UTC | 169 | 339 | 2026-11-15 08:00 / Completed | Active; retain. |
| `lighting/home` | `0 1 * * *` Chicago | 7 | 7 | 2026-11-15 07:02 / Completed | Active; retain. |
| `mcp-atlas/mcp-atlas` | `45 */6 * * *` UTC | 29 | 57 | 2026-11-15 06:45 / Completed | Active; retain. |
| `mcp-fleet-health/mcp-fleet-health` | `12 * * * *` UTC | 169 | 337 | 2026-11-15 07:12 / Completed | Active; retain. |
| `merge-approved/merge-approved` | event disabled | 0 | 0 | none retained | Dormant autofix chain; incoming automation disabled. |
| `phi-atlas/phi-atlas` | `0 13 * * *` UTC | 7 | 15 | 2026-11-14 13:00 / Completed | Active; retain. |
| `phi-memory-synthesis/phi-memory-synthesis` | transform completed | 161 | 325 | 2026-11-15 08:02 / Completed | Active transform consumer. |
| `phi-tag-maintenance/phi-tag-maintenance` | `0 13 * * *` UTC | 7 | 15 | 2026-11-14 12:59 / Completed | Active; retain. |
| `phi-trigger/phi-character-retro` | `0 17 1 * *` UTC | 0 | 1 | 2026-11-01 17:00 / Completed | Keep monthly; Nov 1 run, next due Dec 1. |
| `phi-trigger/phi-chicken-precheck` | `0 4 * * *` UTC | 7 | 14 | 2026-11-15 04:01 / Completed | Active; retain. |
| `phi-trigger/phi-chicken-scout` | `0 18 * * *` UTC | 7 | 16 | 2026-11-14 18:00 / Completed | Active; retain. |
| `phi-trigger/phi-curation` | `0 3 * * 1` UTC | 2 | 3 | 2026-11-11 20:07 / Completed | Active; retain. |
| `phi-trigger/phi-editorial` | `0 15 * * *` UTC | 7 | 16 | 2026-11-14 15:00 / Completed | Active; retain. |
| `phi-trigger/phi-fastmcp-watch` | `42 16 * * 1` UTC | 1 | 3 | 2026-11-09 16:41 / Completed | Active; retain. |
| `phi-trigger/phi-likes-review` | `0 20 * * 0` UTC | 1 | 4 | 2026-11-08 20:00 / Completed | Active; retain. |
| `phi-trigger/phi-pull-review` | event disabled | 0 | 0 | none retained | Dormant autofix chain; incoming automation disabled. |
| `pi-agent/investigate` | external/manual; worker disabled | 0 | 0 | none retained | No activity; exe worker disabled; documented Phi caller. |
| `pi-agent/pi-agent` | manual | 0 | 0 | none retained | Manual runner; conditional retirement candidate. |
| `prefect-home-canary/canary` | release/manual | 9 | 20 | 2026-11-15 07:44 / Completed | Keep: release verification, intentionally unscheduled. |
| `pub-search-atlas/pub-search-atlas` | `0 */6 * * *` default TZ | 3 | 3 | 2026-11-15 06:00 / Completed | Active; recreated Nov 14 after rename. |
| `pub-search-snapshot/pub-search-snapshot` | `40 6 * * *` UTC | 7 | 57 | 2026-11-15 06:40 / Completed | Active; now daily, older runs used higher cadence. |
| `relay-health/relay-health` | `*/5 * * * *` UTC | 1469 | 1469 | 2026-11-15 07:59 / Completed | Active; retain. |
| `report-presence/phone-presence` | external phone | 5 | 5 | 2026-11-15 03:24 / Completed | Keep: external phone caller exercised. |
| `status-page/status-page-bluesky` | `*/5 * * * *` UTC | 357 | 357 | 2026-11-15 08:00 / Completed | Active; retain. |
| `stream-admission/stream-admission` | external CI | 15 | 30 | 2026-11-14 07:47 / Cancelled | Keep: external CI caller; latest run cancelled. |
| `test-pull-boundary/test-pull-boundary` | manual probe | 0 | 0 | none retained | Probe; conditional retirement candidate. |
| `test-pull-patch/test-pull-patch` | merge-approved child | 0 | 0 | none retained | Called by merge-approved; decide with whole chain. |
| `transform/transform` | classify-emails completed | 161 | 328 | 2026-11-15 08:01 / Completed | Active classification consumer; submits Dagster asset work. |
| `watch-fastmcp/watch-fastmcp` | `*/5 * * * *` UTC | 2019 | 4033 | 2026-11-15 08:00 / Completed | Active; retain. |
| `watch-stream/watch-stream` | `*/5 * * * *` UTC | 2018 | 3188 | 2026-11-15 08:02 / Completed | Active; retain. |
| `watch-tangled-pulls/watch-tangled-pulls` | `17 * * * *` default TZ | 168 | 336 | 2026-11-15 07:16 / Completed | Runs hourly; revise consumer disabled. Review paired value. |

### deployment and automation decisions

1. **Remove or disable one stale brief automation in a separately authorized
   cleanup.** Enabled `brief__automation_1`
   `ca5e8859-4f70-4d9b-819d-f0c95faf8c9e` targets deleted deployment
   `c004d049-278a-40d5-bfd0-1de1b2d5dfc8`. Preserve the working automation
   `ec1b5f57-aaaf-402b-8689-31afdb48c409`, which targets current brief deployment
   `3ce724a1-2c7a-4cda-8671-a60740a78345`. This proves a dangling action, not
   duplicate successful brief execution.
2. **Resolve the missing Albedo drift audit.** `prefect.yaml` contains 42 legacy
   definitions and one active schedule: `albedo-drift-audit`, Monday 13:00 UTC.
   It is absent from current production and the home manifest. It was added in
   `d8d5c3729c95a315dc495696b121f7c3c45fb7dc` around cutover. Choose whether the
   weekly audit is still wanted before either registering it or retiring the
   declaration. Do not run a bulk legacy deploy.
3. **Keep autofix intentionally suspended.** Autofix last ran November 6;
   autofix-revise, merge-approved and phi-pull-review have no retained runs and
   their incoming actions are disabled. Decide their future as one chain, with
   `test-pull-patch` (called by merge-approved) and the pull-comment bridge.
   `watch-tangled-pulls` still polls hourly while its revise consumer is disabled;
   confirm whether its independent observation/reconciliation is still wanted.
4. **Review email triage's value and delivery.** Fifteen retained runs comprise
   ten `Unanswered`, one `Quiet`, two `Failed`, one `Crashed` and one `Cancelled`.
   Fourteen were scheduled. This is evidence of no answered cycle in the window,
   not evidence that email ingestion/classification or backlog tooling is unused.
   Decide whether to repair interaction delivery, change the cadence or pause the
   interactive triage after checking its intended consumer.
5. **Review dormant manual endpoints selectively.** `pi-agent/pi-agent` and
   `test-pull-boundary` are conditional retirement candidates if their manual
   use is no longer wanted. `email-backlog` remains an explicitly on-demand
   utility. `pi-agent/investigate` has a documented Phi caller, but its exe pool
   is unavailable; resolve the caller/worker contract before removing it.
   No retained run alone justifies deleting any of these.
6. **Keep recovery, verification and external-trigger deployments.** Canary,
   stream admission, phone presence and the exercised Albedo pilot have live
   usage without schedules. Monthly character retrospective ran November 1;
   zero starts in seven days is expected. The renamed pub-search-atlas was
   recreated November 14, so its short history is not evidence of abandonment.
7. **Treat costs as active but incomplete.** All 22 retained runs are named
   `Incomplete` with completed state type: 14 scheduled and eight other starts.
   The deployment is used; current reports must carry coverage limitations.
8. **Keep analytics maintenance and address its startup dependency failure.**
   Thirteen of 14 retained starts completed. The latest run,
   `f1e66bbe-d622-4e34-a2dc-5119ad7e5558`, crashed November 14 at 09:37 UTC.
   Worker logs show uv timing out fetching the PyPI pdsx index after three
   retries. The later crash-hook error about an existing `flow-source`
   destination is secondary. Last completed run was November 13. This is a
   runtime failure, not an unused deployment; no replacement run was triggered.

There are 34 production automations: 25 enabled and nine disabled. The working
completion chains are ingest→classify→transform, transform→brief/memory, and
phi-atlas→docket. Both FastMCP brief event triggers are disabled while its
four-hour schedule is active. Its 237 retained starts comprise 84 scheduled and
153 other starts; that history does not prove the disabled triggers fire now.
The enabled old laptop `triage ready -> discord` notification is a cleanup
candidate after checking external emitters; its original local producer was
removed October 22.

Five home process pools were READY: background (limit 1), build (1), gardener
(2), interactive (2), and default heavypad (1). `gardener-exe` was NOT_READY,
unpaused, limit 2; its `pi-exe-worker.service` was inactive and disabled on the
host. The registration timer was enabled and its most recent receipt matched
main. No Late or active runs were returned during the per-deployment checks;
that snapshot is not a long-term queue-health assertion.

## Prefect Cloud deployment inventory

The configured bot workspace has **50 deployments**, with **no active schedules**.
There are seven enabled automations, including FastMCP issue/PR intake and
published-release canary triggers. Lack of a schedule does not mean these
pipelines are unused. Deployment IDs, inactive schedules and automation targets
are preserved in the evidence file; names alone are not unique in this workspace.

Four recovery deployments belong to this repository. All are deliberately
unscheduled and were exercised October 26. Keep them as standby paths; a
three-week-old exercise is not proof of current recovery readiness. In particular,
`costs-recovery` completed with `Incomplete` coverage and should not be described
as equivalent to a complete production cost snapshot.

| Recovery deployment | Starts, 7d | Starts since Oct 15 | Last start UTC / state |
| --- | ---: | ---: | --- |
| `bufo-traffic-recovery` | 0 | 4 | 2026-10-26 01:14 / Completed |
| `costs-recovery` | 0 | 2 | 2026-10-26 01:48 / Incomplete |
| `diagnostics-recovery` | 0 | 1 | 2026-10-26 00:58 / Completed |
| `mcp-fleet-health-recovery` | 0 | 1 | 2026-10-26 01:27 / Completed |

Eight FastMCP/canary deployments are externally owned. The six triage, stack and
verification deployments pull `.github/agentic` from PrefectHQ/fastmcp; the two
canary deployments pull prefectlabs/fastmcp-canary. Seven have recent retained
activity. Keep the quiet release-check child with its owning canary workflow
unless that owner establishes it is obsolete; do not delete it in isolation.

| External deployment | Starts, 7d | Starts since Oct 15 | Last start UTC / state |
| --- | ---: | ---: | --- |
| `cloud-canary-check-release` | 0 | 0 | none retained |
| `cloud-canary-intake` | 4 | 4 | 2026-11-13 17:08 / Completed |
| `cloud-contribution-verification` | 70 | 513 | 2026-11-15 04:32 / Completed |
| `cloud-stack-batch` | 91 | 497 | 2026-11-15 04:26 / Degraded |
| `cloud-stack-validation` | 0 | 6 | 2026-10-29 12:26 / Completed |
| `cloud-triage-finalize` | 87 | 584 | 2026-11-15 04:34 / Completed |
| `cloud-triage-intake` | 297 | 917 | 2026-11-15 04:25 / Degraded |
| `cloud-triage-verify` | 63 | 481 | 2026-11-15 04:28 / Completed |

The old `cloud-validation` (`5f1e86fb-d975-47e5-a85d-a45b3dd04865`) and
`cloud-publication` (`b74cd9ba-4596-41e8-84fc-6f0620bd8d4b`) IDs referenced by
this repository return not found. Their absent deployments are a documentation
and experiment-cleanup finding, not evidence that the current FastMCP pipeline
is inactive.

The other 38 Cloud entries below have **zero starts since October 15 and no
retained last start**, and none has an active schedule. They appear to be shared
workspace test/demo fixtures. They are cleanup candidates for an owner-confirmed
workspace sweep, not established production dependencies of this repository.
Ownership and external/manual consumers were not established for each fixture.
Keep this scope distinction before deleting any of them.

| Flow / deployment | Deployment ID | Paused | Pool |
| --- | --- | --- | --- |
| `a-bunch-of-operations/a-bunch-of-operations` | `1efe5a27-8559-4b47-91c1-83a7a022b555` | yes | `none` |
| `aio-test-flow/aio-test` | `29f8d1a9-ca9b-4225-9009-ac6813459f5b` | no | `none` |
| `async-deployment-flow/async-test-deployment` | `5552c124-e900-445b-b3d1-2e0eb1db9ff1` | no | `none` |
| `concurrent-flow/concurrent-0` | `c1d1502f-3dcc-46be-929e-e61e5e6781cd` | no | `none` |
| `concurrent-flow/concurrent-1` | `891621f0-0c87-48ea-afd6-e42af86c1558` | no | `none` |
| `concurrent-flow/concurrent-2` | `99a3abfc-1146-4e02-b8c2-8db593ea26a2` | no | `none` |
| `concurrent-flow/concurrent-3` | `b5eb6cd4-cf02-4b6a-a127-43563b74ea2b` | no | `none` |
| `concurrent-flow/concurrent-4` | `6224b1bd-8d8b-4086-ad12-4cc90794645f` | no | `none` |
| `demo-secrets-flow/deployment-with-custom-secrets` | `4937571c-c76f-414b-af40-4fe9594b3435` | no | `none` |
| `check-env/env-test` | `aebef42b-b8c8-436b-a595-4724b1f7eea5` | no | `test-process-pool` |
| `event-pipeline/event-pipeline` | `d5fe22cf-2f63-4155-a934-de010392cb13` | no | `none` |
| `explicit-deployment-flow/explicit-deployment` | `4fd66c5c-eb6a-433d-a62e-eabe3fa2958d` | no | `none` |
| `process-data/import-string-demo` | `c236e230-4248-43b0-b182-56d32c7a9670` | no | `none` |
| `inner-flow/inner-runner` | `77f5e8f0-c408-4dd4-88fa-c978cd93f9df` | no | `none` |
| `None/managed-simple-flow` | `55f3a64f-866f-4e0e-9cf9-01c9d64a17b0` | no | `managed` |
| `module-flow/module-level-runner` | `33efe87c-b198-45a5-9fe2-07c860936e79` | no | `none` |
| `ingest-orders/order-ingestion` | `63da3297-c5ca-4ac5-83c6-e391412c2423` | no | `none` |
| `my-test-flow/provenance-test-deployment` | `10b3e6f3-1327-40cf-982a-13626e680e53` | no | `none` |
| `None/rowdy` | `cd9e0d01-6cc9-4705-9278-7f4f82bc187d` | no | `local` |
| `my-flow/runner-28dada18-b6c3-44a5-9cba-74d5b07b3fc8` | `b45c583b-0ff8-436a-b991-85fb7e47974b` | yes | `none` |
| `my-flow/runner-f1e73cff-3dc1-4c0b-a49a-23e87398cb60` | `2674760b-342f-4945-a8b4-df892bfde818` | yes | `none` |
| `None/spcs-e2e` | `31cded69-f02f-4967-9a1e-58c61cd52b7f` | no | `spcs-e2e` |
| `parent-flow/test` | `3924aca8-0b50-4f44-9e18-0d232a07acb3` | no | `none` |
| `deployment-flow/test-deployment` | `878cb2a7-85c8-4749-bfe5-5f0657433f43` | no | `none` |
| `None/test-deployment` | `b36cb565-337f-44d5-a81d-9e2fddc7aa2f` | no | `none` |
| `None/test-deployment-3557b5c4` | `349d71e1-144a-438e-b4f6-c4989ef77fbd` | no | `none` |
| `None/test-deployment-9850c17a` | `f15ac0a3-2ccc-4320-aafd-166159608b87` | no | `none` |
| `None/test-int-interval` | `7ae316cb-7328-430f-bed4-16bfa65293a0` | no | `local` |
| `None/test-iso-interval` | `05b9bbb0-3f3e-4963-b03d-d3256103b992` | no | `local` |
| `my-flow/test-runner` | `6fb75e67-e3ca-45d3-97d7-90a145216c8b` | no | `none` |
| `my-async-flow/test-runner-async` | `fd96a836-bc38-4cb4-926f-a7c544bad320` | no | `none` |
| `async-override-flow/test-runner-async-override` | `65e0a29f-6501-4f7e-a307-41427cb5a3d2` | no | `none` |
| `explicit-async-flow/test-runner-explicit-async` | `6d892307-58c5-4796-9be8-2cf3c6d567a7` | no | `none` |
| `override-flow/test-runner-sync-override` | `4a5cde37-c7be-4899-b412-ca20bfb281b5` | no | `none` |
| `pfs-with-id/test-suspend-deployment` | `fd05b2f6-9d42-4853-ba6f-dff128485707` | no | `none` |
| `None/warning-test-0` | `14c63a9a-ca6d-46eb-90a0-28dc55778a14` | no | `none` |
| `None/warning-test-1` | `8ca4e987-badb-4b0e-968a-2f3fc281c46c` | no | `none` |
| `None/warning-test-2` | `3e77b9a0-b119-4ce8-aab2-bfb69818e7f3` | no | `none` |

## suggested implementation order

1. Reconcile the current home runbook and deployment/access skills, including
   explicit target/manifest and host registration ownership. Correct the
   legacy-target helper recipes in a small, separately verified code change.
2. Refresh current hub, worker, backup, presence, FastMCP and recovery guidance;
   move or label migration-era procedures. Add a few README entry links and fix
   the four broken references. Keep the dated evidence intact.
3. Make the Albedo drift, suspended autofix chain, dormant manual endpoints and
   unanswered email-triage decisions with their consumers visible. Apply the
   service-retirement procedure where an externally consumed service remains.
4. Perform the precisely scoped stale-automation cleanup, then re-read live
   automation/deployment state. Audit shared Cloud fixtures with their owners
   separately from production cleanup.

## validation and limits

`just validate-deployments --config prefect.heavypad.yaml` passed with zero
errors. It reported unverified runtime routes; static validation does not prove
artifact availability, worker readiness or successful execution. Live queries,
registration state and selected worker logs supply the operational evidence
above. No full application test suite was needed for these audit-only artifacts.

The report and evidence were checked for inventory counts, target references and
formatting. Existing untracked `deploy/hcloud-reaper/` and
`docs/audits/2026-11-08-fly-apps.md` were preserved. No current operating document,
production configuration, scheduler, Cloud fixture or service was modified.

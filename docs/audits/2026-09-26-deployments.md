# Prefect deployment audit — 2026-09-26

**Corrected after checking origin/main:** the audit used local HEAD `abbd167`, which was 18 commits behind `origin/main` (`268b9a0`). Live run observations remain evidence, but the original recommendations to disable typeahead backfill and change Tangled polling to 15 minutes were wrong or superseded. The handoff of that time (`HANDOFF.md` at `268b9a0`, since folded into these docs) recorded an already-decided persistent Stream subscriber with hourly reconciliation. Commit `4b893e0` deliberately re-enabled typeahead backfill. No production change was made.

The platform is operationally healthy at the snapshot, but some green runs conceal incomplete outcomes. Keep the core ingestion, publishing, and health workflows. Treat generic manual utilities and unanswered email prompts as candidates for discussion, preserving the connected Gardener review/test/merge chain.

This was a read-only production audit. No deployment, schedule, automation, light, or flow-run state was changed.

## Status, 2026-09-28

- **Done.** Finding 2: `watch-tangled-pulls` is an hourly reconcile behind a
  persistent Stream subscriber (`deploy/pull-comment-bridge/`). Finding 1: the
  resumed backfill ran; its 09-27 failure (a Turso `SQLITE_IOERR` result) is
  now retried. The zig server now applies bulk-delete filters, clears stale
  scheduled runs, and honors `for_each` (prefect-server CHANGELOG).
- **Also done 2026-09-28.** Finding 3: `mcp-fleet-health` findings route to
  Discord (`deploy/automations.yaml`). Finding 4: `costs` ends
  `Completed(name="Incomplete")` and names what it could not measure. Finding
  10: `docs/deployments.md` covers `deploy/presence.yaml`, names `mcp-atlas`,
  and counts pools.
- **Still open**, tracked as tangled issues on this repo: the retire/pause
  decisions (findings 5–7), per-run `for_each` on the failure automations,
  measuring R2 and stopped Fly rootfs, `watch-fastmcp`'s push source, and a real
  operator-comment test of the pull-comment bridge.

## Evidence and limits

- Server: `https://prefect-server.waow.tech/api`, accessed through the repository's authenticated `just prefect` recipe. Local Prefect MCP identity timed out; the hosted Cloud connector was not used.
- Inventory: **44 live deployments**, **24 enabled automations**, **3 READY pools** (home, laptop, Sprite). The stale local YAML declares 41; current origin/main declares 42 including fastmcp-triage. Phone-presence and separately owned mcp-atlas account for the remaining difference.
- Queried start times from **August 27 through September 26, 19:51 UTC (2:51 PM Chicago)**. Paginated 21,476 unique runs and matched the API count. Of these, **21,079 belong to the current 44 deployments**, 388 have no deployment, and 9 belong to no-longer-registered deployments.
- All retained deployment-backed runs in that query start on or after **September 12, 19:53 UTC**. Treat the observed deployment history as approximately 14 days, not a complete month. The cause of the older-history gap was not established. A zero below means no observed run, not never used.
- Current deployments: **14 Failed**, 101 ingest Degraded, 16 diagnostics Degraded, 2 autofix Degraded. The last 24 hours contain **1,538 runs: 1,531 Completed, 3 Diagnosed, 3 Failed, 1 Quiet**. All three recent failures were phone-presence.
- Open-run query found no overdue scheduled, pending, running, paused, or cancelling runs before the snapshot cutoff. This is a point-in-time observation.
- Read the latest retained log tail for all **35 deployments with runs**, plus selected failure logs, live schedules/automations, declared purpose and callers, and relevant source at deployed revision `268b9a01`. Used the actual older autofix pin and private presence bundle as caveats instead of equating local HEAD with production.
- Counts describe orchestration outcomes. Published artifacts were evidenced in logs; typeahead's serving build was additionally checked live. Bot-side Phi outcomes and every downstream consumer were not independently verified. Run duration is elapsed time, not CPU time or a dollar cost.

## Findings to act on

1. **Correction: typeahead backfill is deliberately scheduled.** Commit `4b893e0` re-enabled its daily schedule after an accidental pause. The live schedule matches current origin/main. Keep it enabled; the handoff calls for verifying its first run and ingestion health, not disabling it.
2. **Follow the already-decided subscriber migration.** The `268b9a0` handoff specified a persistent systemd Stream subscriber on heavypad, durable cursor/deduplication, Prefect events triggering autofix-revise, and hourly reconciliation without Stream draining. Current flow code still opens a short-lived subscription on each two-minute run (8-second idle exit, 120-second cap); the persistent subscriber is planned, not implemented in origin/main. Observed 10,079 runs and 31.6 elapsed hours support eliminating repeated startup, but a 15-minute cron change was not the agreed direction.
3. **MCP health alerts miss the Discord route.** The deployed flow emits `fleet-health.unhealthy` with resource ID `mcp-fleet-health`; the enabled `fleet unhealthy -> discord` automation matches only resource ID `fleet-health`. Widen that match. Both health flows may finish Completed when reporting unhealthy services, so run-success percentages alone are insufficient. The latest MCP run reports 8/8 healthy.
4. **Costs are incomplete despite Completed.** The September 26 run explicitly reports unauthorized Cloudflare R2 analytics and unmeasured root filesystems on 11 stopped Fly machines, then publishes a snapshot. Fix access/measurement where possible and surface known incompleteness in the state/report. Do not use its reported total as a complete bill or infer deployment savings from it. Existing `COSTS.md` attribution caveats still apply.
5. **Email triage has weak demonstrated value.** Eight observed runs: four Unanswered, three Quiet, one Completed. Its code only records decisions; it does not perform mailbox actions. Recommend pausing the daily prompt unless this remains the desired interface. Quiet runs are valid, not failures. Ingest/classification remain useful independently.
6. **Three clear manual retirement candidates:** `pds-records`, `dep-bump`, and `pi-pr`. None has an observed run or a caller in the inspected live automations/repository. PDS CRUD already exists in pdsx; the others can remain source-level/manual utilities. External callers were not exhaustively searched, so these are recommendations, not proven safe deletions. `pi-agent` is a weaker fourth candidate: two completed runs, latest a canary-style read-only prompt.
7. **Do not mistake dormant dependencies for orphans.** Live proposal automations target `phi-pull-review` and `merge-approved`; the watcher invokes `autofix-revise`; merge invokes `test-pull-patch`. Keep this set if the Gardener loop is wanted, or retire the set and its automations together. READY worker status and static configuration do not establish runtime readiness for the zero-run Sprite jobs.
8. **Phi trigger success means acceptance.** Curation, likes, editorial, chicken, and character-retro share one HTTP trigger flow. Their distinct schedules/parameters are legitimate deployments. Add correlation with bot-side completion/output if stronger health claims are needed. A monthly deployment with no retained September 1 history should not be retired for inactivity.
9. **Recoveries, not active incidents:** email ingestion degraded 101 times September 15–19 and has recovered; diagnostics' 16 Degraded runs flagged sustained process CPU burn; tag maintenance hit a provider 529 and recovered; pub-search's one failure was a document-count safety gate; stream admission had two failures, latest a RocksDB lock conflict. Presence connectivity recovered after physical switches were restored, but no verification rerun of the flow was requested or performed.
10. **Inventory and deployment provenance need clearer documentation.** Current origin/main declares 42 deployments, including laptop-pool fastmcp-triage; the generated page still incorrectly claims all use home-pool. Two jobs use the Sprite pool. Phone-presence and mcp-atlas are separately registered. Presence uses a private bundle and selected Gardener jobs use wheels/older pins; preserve those loading modes. Comparing live configuration only to this stale checkout created the false backfill finding above.

## Every live deployment

Observed state counts use Prefect state names, preserving Degraded, Quiet, Unanswered, and Diagnosed. Deployment names link to the latest observed run where one exists. Recommendations combine live usage, declared purpose, inspected callers, and sampled outputs; product usefulness still depends on what you want to keep using.

| Deployment | Observed runs by state | Recommendation and evidence |
| --- | --- | --- |
| [autofix](https://prefect-server.waow.tech/flow-runs/flow-run/deb90e73-45a4-4eeb-bb5b-9ea34a8d9f54) | 2 Degraded; 22 Diagnosed | Keep, tune: 22 diagnoses, 2 Degraded. Deduplicate recurring failures and distinguish physical conditions from code defects; presence advice was overconfident. |
| `autofix-revise` | 0 observed | Keep dormant if Gardener stays: watcher invokes this; zero runs is unexercised functionality, not an orphan. |
| [bisk-snapshot](https://prefect-server.waow.tech/flow-runs/flow-run/a258c396-2d62-48dd-a545-298d7591d667) | 2016 Completed | Keep: authoritative product snapshot; latest published 560 bisks from 151 chickens. |
| [brief](https://prefect-server.waow.tech/flow-runs/flow-run/116e98ad-edc4-4713-95ac-a08213855158) | 247 Completed | Keep if the hub briefing is read; consider change-driven generation. Latest wrote briefing.json from 215 items. |
| [bufo-traffic](https://prefect-server.waow.tech/flow-runs/flow-run/ae7a863b-4455-44d1-b3ec-da6a6d3394e4) | 336 Completed | Keep: latest wrote 3 daily traffic records. Lower cadence only if hourly reporting is unnecessary. |
| [classify-emails](https://prefect-server.waow.tech/flow-runs/flow-run/b1187db7-f502-4875-aca7-3872124d48ef) | 247 Completed | Keep in the ingest chain. Latest correctly found no unclassified emails; no-op is expected. |
| [costs](https://prefect-server.waow.tech/flow-runs/flow-run/bea8fd5c-1aff-4b2c-8910-3774a34a0b26) | 14 Completed | Keep, fix completeness: R2 storage unauthorized; 11 stopped Fly machine root filesystems unmeasured despite Completed. |
| `dep-bump` | 0 observed | Retirement candidate: no observed runs or repository caller. Preserve source as an on-demand utility unless this is still the intended dependency rollout path. |
| [diagnostics](https://prefect-server.waow.tech/flow-runs/flow-run/dd0f6efd-9686-42a9-80b1-18dcbd41fbc9) | 320 Completed; 16 Degraded | Keep: distinct worker telemetry/canary; historical Degraded states identified sustained CPU burn. Latest host has ample headroom. |
| [docket](https://prefect-server.waow.tech/flow-runs/flow-run/41dd4d5b-ee1d-4345-8ce6-b5f5846f5381) | 14 Completed | Keep: depends on phi-atlas and publishes promotion material; latest emitted 10 candidates and wrote PDS record. |
| [email-triage](https://prefect-server.waow.tech/flow-runs/flow-run/00905dbe-9f32-48d3-8632-c3f382b992ca) | 1 Completed; 3 Quiet; 4 Unanswered | Pause candidate: 4 Unanswered, 3 Quiet, 1 Completed. Decisions are recorded, not executed on the mailbox; retain daily prompting only if this interaction is useful. |
| [fastmcp-brief](https://prefect-server.waow.tech/flow-runs/flow-run/efe22e09-d703-4c90-8c6b-56384f9066d0) | 116 Completed | Keep as selector for triage; review overlapping scheduled/activity/direct-ask triggers. Thread deduplication already exists. |
| [fastmcp-triage](https://prefect-server.waow.tech/flow-runs/flow-run/1ec7ec56-bbd3-41b6-baf0-e8fe2ca69db5) | 5 Completed | Keep: new today, 5 completed runs; laptop-pool, triggered by FastMCP briefs. Latest skipped previously triaged/claimed items appropriately. |
| [fleet-health](https://prefect-server.waow.tech/flow-runs/flow-run/2f0ebf5e-e3dd-4a72-bd12-527e5cfe5f88) | 1345 Completed | Keep: broad service/stream health; latest sampled checks healthy. Completed is not a guarantee that every service was healthy historically. |
| [ingest](https://prefect-server.waow.tech/flow-runs/flow-run/27269456-2295-46ad-b600-b6d1165b9bb0) | 238 Completed; 101 Degraded | Keep: core pipeline. 101 Degraded runs were email-unavailable, last September 19; latest fetches and persists emails successfully. |
| [leaflet-atlas](https://prefect-server.waow.tech/flow-runs/flow-run/00750340-16ad-47ec-8ced-bfadabfa06bb) | 56 Completed | Keep if semantic map remains wanted; latest actually deployed Pages. Candidate for daily/change-driven rebuild rather than every six hours. |
| [mcp-atlas](https://prefect-server.waow.tech/flow-runs/flow-run/de87a9d3-064b-4ffa-a26b-0f435b1953db) | 56 Completed | Keep: separate repository, not an orphan. Latest published 13 servers from 7 DIDs; distinct from health checking. |
| [mcp-fleet-health](https://prefect-server.waow.tech/flow-runs/flow-run/2d332125-9ef1-47da-b7f7-ae6ae0e7ca7c) | 337 Completed; 1 Failed | Keep, fix alert routing: live unhealthy automation excludes its resource ID. Latest 8/8 servers healthy; one older discovery error recovered. |
| `merge-approved` | 0 observed | Keep dormant with Gardener: live proposed-pull automation invokes it, and it invokes test-pull-patch. No recent end-to-end runtime proof. |
| `pds-records` | 0 observed | Strong retirement candidate: no observed runs or automation caller; generic CRUD duplicates configured pdsx tooling. Retain source if its bulk workflow is useful. |
| [phi-atlas](https://prefect-server.waow.tech/flow-runs/flow-run/fa3c4af9-40b3-4351-97d5-dd309405d965) | 14 Completed | Keep: latest published 8,871-point atlas; docket consumes it. Not interchangeable with tag maintenance or memory synthesis. |
| `phi-character-retro` | 0 observed | Keep pending evidence: monthly, next run October 1. Retained deployment history does not cover September 1; zero is not evidence of disuse. |
| [phi-chicken-precheck](https://prefect-server.waow.tech/flow-runs/flow-run/1d43d481-a510-4a4e-a838-b733d770346c) | 13 Completed; 1 Failed | Keep if chicken participation is wanted. One September 16 timeout; subsequent requests accepted. Bot-side completion not verified. |
| [phi-chicken-scout](https://prefect-server.waow.tech/flow-runs/flow-run/9a996071-f4df-414a-9572-1d16cc74bbb7) | 14 Completed | Keep if chicken participation is wanted; all requests accepted. Bot-side outcomes not verified. |
| [phi-curation](https://prefect-server.waow.tech/flow-runs/flow-run/06c07f9f-5f60-4143-87bd-d1a787b716b9) | 2 Completed | Keep: weekly slot, both requests accepted. Verify resulting publications separately before calling product outcome healthy. |
| [phi-editorial](https://prefect-server.waow.tech/flow-runs/flow-run/5e4d5844-cba2-4a08-b7da-016437014adf) | 14 Completed | Keep for coral context: daily requests accepted. Actual bot-side refresh not verified by this flow. |
| [phi-likes-review](https://prefect-server.waow.tech/flow-runs/flow-run/da684948-8e91-4418-a51c-33a1415ecccd) | 2 Completed | Keep: weekly request accepted; complements memory synthesis with follow-ups/cards. Bot-side outcome not verified. |
| [phi-memory-synthesis](https://prefect-server.waow.tech/flow-runs/flow-run/d1141ec3-aa08-4edc-8d98-e5049237d041) | 247 Completed | Keep: latest extracted 8 observations, 4 actionable. Consider lower cadence if needed, accounting for existing content-based caches. |
| `phi-pull-review` | 0 observed | Keep dormant with Gardener: live proposed-pull automation invokes it. Zero runs does not justify isolated deletion. |
| [phi-tag-maintenance](https://prefect-server.waow.tech/flow-runs/flow-run/58a9ddd5-56d7-4f80-8fea-8ebf237708fa) | 13 Completed; 1 Failed | Keep: latest normalized 24 observations and stored 80 tag relationships. One provider-overload failure September 23 recovered. |
| [phone-presence](https://prefect-server.waow.tech/flow-runs/flow-run/bf42a4d3-0212-43a7-9386-87d882144588) | 26 Completed; 8 Failed | Keep: 8 historical failures; latest was switched-off bulbs. All 13 lights now connected, but the flow was not rerun during this audit. |
| [pi-agent](https://prefect-server.waow.tech/flow-runs/flow-run/6c495b77-cc41-440b-83ff-3b6815fa5aff) | 2 Completed | Optional/manual-only: 2 completed runs, latest a read-only canary-style prompt. Retire if no remote ad-hoc agent consumer is intended. |
| `pi-pr` | 0 observed | Retirement candidate: no observed runs or automation caller. Overlaps other coding-agent entry points; preserve only if this manual Gardener interface remains wanted. |
| [pub-search-snapshot](https://prefect-server.waow.tech/flow-runs/flow-run/83d4e0c8-abc9-4f52-b7e8-3b678cd511a3) | 167 Completed; 1 Failed | Keep: latest published 99,315 documents. September 21 failure was DocCountGate, a protective gate; subsequent publishing recovered. |
| [strata-hourly](https://prefect-server.waow.tech/flow-runs/flow-run/5ac912d0-2724-4393-86ab-d7e8893c02a4) | 336 Completed | Keep: latest ingested 1,019 segments, demonstrably useful work; distinct from service health checks. |
| [stream-admission](https://prefect-server.waow.tech/flow-runs/flow-run/e7effe47-50b4-4a2f-985c-ac432a4def0b) | 18 Completed; 2 Failed | Keep: active manual CI gate, latest PASS/receipt. Latest historical failure was a RocksDB lock conflict in power-loss testing. |
| `test-pull-patch` | 0 observed | Keep dormant with merge-approved: called by the merge gate; uses Sprite pool. Zero runs means runtime readiness remains unproven. |
| [transform](https://prefect-server.waow.tech/flow-runs/flow-run/61b88ce5-0381-4d2c-a856-15704d875bc2) | 247 Completed | Keep: dbt models and hub.duckdb export underpin downstream jobs; 247 completed runs. |
| `typeahead-enrich-backfill` | 0 observed at cutoff | Keep scheduled: intentionally re-enabled in `4b893e0`; original inactive-schedule recommendation was based on stale local YAML. Verify first resumed run. |
| [typeahead-identity-hourly](https://prefect-server.waow.tech/flow-runs/flow-run/61a0fccf-e90c-46e8-abb9-b5b5796b66a3) | 336 Completed | Keep: latest checked 1,275 actors and resolved 554; distinct incremental identity path. |
| [typeahead-index](https://prefect-server.waow.tech/flow-runs/flow-run/4cf7c8eb-575f-400d-89c3-c2a0cad1bff9) | 5 Completed | Keep: published build is confirmed serving and fresh. Last run skipped cleanup while freshness endpoint unavailable; endpoint now healthy. |
| [typeahead-plc-identity](https://prefect-server.waow.tech/flow-runs/flow-run/d17c2ea7-8e58-4974-9963-2cfbfa034794) | 2 Completed | Keep: latest bulk pass resolved 5,492 identities; complements hourly resolution. Review full 122-million-op rescans as an optimization, not a deletion reason. |
| [watch-fastmcp](https://prefect-server.waow.tech/flow-runs/flow-run/41fe5f95-4719-448b-be8d-2e35343f2e9a) | 4032 Completed | Keep: supplies events to brief/triage; latest 304/no change is expected. Consider 10–15 minute cadence if latency is acceptable. |
| [watch-tangled-pulls](https://prefect-server.waow.tech/flow-runs/flow-run/349d9273-df03-47c0-8ba9-fa830033863d) | 10079 Completed | Done 2026-09-28: persistent Stream subscriber plus hourly reconciliation; supersedes the original 15-minute polling recommendation. |

## Suggested order

1. Verify the intentionally resumed backfill; repair MCP alert routing and cost completeness reporting.
2. Follow the existing persistent-subscriber design from the `268b9a0` handoff (done 2026-09-28), with hourly reconciliation and end-to-end verification before retiring two-minute polling.
3. Decide whether daily interactive email triage and the three unused manual deployment interfaces are still wanted; retire their registration only, preserving reusable source where useful.
4. Keep the connected Gardener chain until deciding on it as a whole. Test its runtime with a separately authorized canary before relying on it.
5. Consider lower/change-driven brief, memory, and atlas cadence only after inspecting consumer freshness needs and existing caches. Retaining separate deployments for separate products and failure boundaries is appropriate.

No measured model-spend rollup or billed-cost analysis was performed; timing and frequency reductions are not dollar savings estimates.

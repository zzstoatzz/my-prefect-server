# September 9 alert triage

Investigated live around 05:05–05:12 UTC (00:05–00:12 Chicago). Scope: the pasted FastMCP digest, studio failure, PDS mirroring alerts, missing typeahead heartbeat, and pub-search snapshot failure.

## Typeahead: recovered; exit-path defect remains

Fly machine `7845636a3d0ee8` panicked at 04:18:43 UTC in Zig 0.16 `std/Io/Threaded.zig:1797`. Last ingestion progress was 04:18:49. At 04:29:21 the watchdog logged that it was exiting after 632 seconds without an event and 637 seconds without a successful flush. The machine never exited: its event history still showed September 5's start, and `/proc/633/task/*/wchan` showed the main thread accepting connections and the other threads waiting on futexes.

Restarted that machine at 05:08:22. It reconnected to the relay from saved cursor `33458998972`, then advanced through `33459060478` by 05:10:31 with 17,600 actor operations logged. Logfire independently confirmed `ingest.heartbeat` at 05:08:28 and 05:09:29. Ingestion and telemetry resumed; completion of backlog replay has not been established.

This repeats the failure described in typeahead commit `5103f96`, despite the deployed logfire-zig 0.3.2 upgrade. The watchdog still calls `logfire.flush()` before `std.process.exit(1)`. The logging wrapper has bounded flush arguments, but the local OTEL batch processor enters `lockUncancelable` before its timed waits. That is a plausible remaining deadlock path, not a captured stack proving the exact mutex involved. A durable repair must make fatal termination independent of the telemetry runtime; the original Zig assertion also needs a separate reproduction. No source edits or new deployment were made during triage.

## Studio: Gemini 429; source and credentials are accessible

[Failed run](https://prefect-server.waow.tech/flow-runs/flow-run/def578c8-5f0b-40c8-ae52-2ad66e393a14) ran the musician studio pinned to plyr.fm commit `d261625f3ebd63307b6b666d8d154ae2e4840a22`. The local checkout was older; inspecting the pinned Git object exposes the actual implementation.

The `interpret-audio` task failed with HTTP 429 on its initial request and all three retries (15, 45, 120 seconds). The flow failed for musician reed after about five minutes. The error class retains only the status code, discarding the provider response details and retry hints. Consequently these logs cannot distinguish short-term rate limiting from exhausted quota. The missing text fallback is intentional: audio listening must not be replaced by an invented text review.

Heavypad retains `/home/stoat/prefect-analytics/musician-studio/2026-09-09-0-reed`, and the deployment supplies its encrypted credential-file path. The autofix claim that another repository makes further investigation impossible is incorrect. Next repair: retain safe quota diagnostics and provider retry hints, then choose retry/defer behavior based on the actual error. No additional generation or publishing run was triggered.

## PDS mirroring: one track, three failed operations

Logfire confirms one initial upload failure at 04:03:01 and two portal save failures at 04:03:37 and 04:05:15 for track 1288, “DO NOT REACT”, owner `did:plc:cbbckcpqr6krasabam2slxza`. Each operation exhausted four attempts with `ReadError`. Each portal failure emits two matching log messages, explaining the alert's five matching rows. Repeated notifications refer to those same historical rows; the latest matching failure remained 04:05:15 at inspection time.

The destination is Blacksky. A token refresh and DPoP retry ultimately produced a successful metadata `putRecord` at 04:03:02. Later audio retries still failed, so token expiration alone does not explain the complete incident. No HTTP response status for the failed blob requests was retained. The source sends a streaming body with Content-Length; transport/proxy behavior and large-body rejection remain hypotheses, not established causes.

The public track API still reports `audio_storage=r2`, `pds_blob_cid=null`, and an AT record URI. Its audio endpoint redirects to `https://audio.plyr.fm/audio/cc7a385cd2dca8bd.wav`, which returns HTTP 200 with Content-Length 49,595,630. Audio is available; PDS mirroring remains incomplete. The failing traces also contain unclosed aiohttp sessions/connections, suggesting cleanup needs inspection when upload consumption exits early. No user record or upload was mutated during investigation.

## Pub-search: integrity gate rejected three snapshots

[Failed run](https://prefect-server.waow.tech/flow-runs/flow-run/548aef4c-ae99-42c7-b114-c08938928acc), 04:40–05:04 UTC, built successfully but failed `DocCountGate` on all three attempts:

| Gate time UTC | Built | Expected | Allowed difference |
| --- | ---: | ---: | ---: |
| 04:48:54 | 87,094 | 86,938 | 86 |
| 04:56:21 | 87,098 | 86,957 | 86 |
| 05:04:31 | 87,106 | 86,926 | 86 |

No rejected artifact was published. Earlier 00:40 and 02:40 runs completed. The serving `/snapshot` endpoint reports build `b1788856887-834b`, 86,826 documents, source watermark September 8 08:41:18. Its configured adoption hour is 08 UTC, so the older serving snapshot is not by itself evidence of failed adoption.

The gate compares paginated export with a later source count using `indexed_at <= watermark`. The comment claims the set below that watermark is immutable, but source upserts can move `indexed_at` forward, and deletes/policy changes also remove rows. Therefore the watermark does not provide a transactional snapshot. Source churn is a plausible explanation for the consistently larger exported counts; this investigation did not identify the exact changed rows. Repair should reconcile mutable membership or use a consistent source view, preserving the publication gate rather than simply increasing tolerance.

## FastMCP digest: queue items and an incorrect reference

- [#4970](https://github.com/PrefectHQ/fastmcp/pull/4970) is Nate's own PR. Open, behind main, review required; reported code/test checks succeeded. Current diff includes const rendering, text error extraction work, and documentation changes following the earlier automated review. This alert is not a production failure.
- [#4899](https://github.com/PrefectHQ/fastmcp/issues/4899) is an open, unassigned client hydration bug. The reported mapping lacks date/time/duration/UUID entries. Related proposals #4900, #4906, #4921 are closed. No new reproduction was run during this operational triage.
- [#5026](https://github.com/PrefectHQ/fastmcp/pull/5026) is open and awaiting review. It changes pagination termination from cursor truthiness to non-null checks. The visible checks were workflow/issue-link checks, not a demonstrated full test matrix.
- The digest's #5018 returns 404. The matching [slugify PR is #5020](https://github.com/PrefectHQ/fastmcp/pull/5020), closed without merging by the contribution assignment gate, linked to #5017. “Waiting on maintainer review to merge” is an inaccurate account of its current state.

## Access and changes

Production Prefect CLI access worked via `just prefect`; the MCP identity call failed to connect. `just heavypad-status` confirmed an active worker, healthy local endpoint, and 48% disk usage. Only `home-pool` was listed. These incidents do not indicate a common worker outage.

The status recipe also reported local main `58a6c189` versus GitHub mirror main `0b9e77c0`; this is a separate code-delivery discrepancy to inspect, not an established cause of these failures. Existing local changes in this checkout and typeahead were preserved. The only production mutation was the typeahead machine restart. No issues, PRs, comments, deployments, or user-content changes were published.

## Evergreen redesign and monitoring follow-up

Evergreen source is `zzstoatzz.io/evergreen`. The redesign on main (`64bea54`)
records 35 projects, 106 observed resources and 46 endpoint checks. It includes
Stream, Rally, Doodl, ZDS and Bird’s Place; project search, attention and resources
views; repository/COSTS.md links; and explicit unassigned ownership and missing
coverage. Cloudflare and Fly inventories are current observations; Hetzner and
Neon resources come from the dated cost snapshot. This is not a complete census
of Wisp, Horizon, Durable Objects, or home hardware.

The existing Worker was deployed as version
`c24bcdb4-0d34-4590-a12b-a00ff9305f02` at
https://evergreen-proxy.n8-3e9.workers.dev. All 46 checks passed both live and
through the Prefect validator. `/costs` proxies the existing public hub snapshot
because browser reads from the hub failed CORS. Cost attribution uses declared
provider/resource keys, not the known-wrong historical project labels: the
2026-09-08 snapshot has $541.76 in recorded monthly costs, including estimates,
with nine unassigned cost lines. Missing cost data is never rendered as zero.

The flow is isolated on `codex/evergreen-health`, commit
`a27fe99c2fa84f96919d3b55820722560a09d7fe`, pushed to both remotes. Deployment
`55784648-253f-4077-9afd-3aaf63adc6cb` is registered on home-pool with that package
pin. Its quarter-hour schedule is **inactive** pending the static-site repair.
No production run has been triggered. The terminal-failure route is the existing
Logfire `flow run failed` alert consumed by phi, not another notification system.

Outstanding: nate.tngl.io still serves the old site after successful pushes;
`/services.json` returns 404. The Tangled hosting settings require an authenticated
browser session. A sign-in is open in the task browser for Nate to complete.
Once authenticated, inspect the Evergreen Sites settings, retain the existing
domain, set/verify main and /site, repair/redeploy, confirm the public inventory
matches the Worker, re-register the canonical prefect.yaml with MPS_PIN set to
the full flow commit, then trigger evergreen-health and verify completion.
Do not enable the schedule while the public inventory is missing.

Validation: 15 Prefect tests and five JS contract tests passed; ruff and ty passed.
Local UI inspected at 390 and 1280 pixels in its single dark theme, including
populated and expanded projects, attention/resources, empty search, loading,
cost-source errors, inventory errors, monitor errors and a fixture HTTP 503.
Phone document width was exactly 390 pixels; details kept the 20-pixel gutter.
The static site has **not** been visually verified in production because the
hosting deployment remains stale. Preview server is localhost:8765.

## Hub consolidation and Discord cleanup — shipped follow-up

Hub's `/projects/` now owns the expanded inventory: 35 projects, 106 observed
resources, 50 endpoint checks, plus Stream's deep check. The source is the mps
package's `projects.json`; the existing fleet-health flow writes a stored report
that Hub reads. The costs collector and Hub use the same ownership mapping.
The current $541.76/month snapshot is partial/estimated, with nine unassigned
lines; it is not a complete bill.

Hub image `9048093` is deployed. Fleet and costs deployments are pinned to
`d4df6d8a876b7db877a6120b5325ea8cb316c046`. Fleet's real run
`7e66b14b-25d5-4796-9534-3139c790dd75` completed with all 50 endpoints and Stream's
deep check passing. Fleet remains scheduled every 15 minutes. Findings reach
phi through the existing Logfire push channel, while checker execution failures
continue through the flow-failure alert. The never-run duplicate evergreen-health
deployment was deleted.

The Prefect Discord flow-failure template now has a short status, run name, and
run/log link. Fleet messages carry up to five bounded findings and a Hub link,
with the original full array preserved for machine consumers. The duplicate
Discord channel was removed from Logfire's `flow run failed` alert; its phi channel
remains active. Other Logfire alerts retain their existing rendering. No synthetic
Discord message was sent; templates/configuration and bounded formatting were
verified, not a new Discord delivery.

Evergreen commit `97b5557` redirects both static entry pages to Hub. The public
nate.tngl.io host still serves an older revision, so the legacy Worker is retained
until Tangled hosting is corrected. Hub's Cloudflare Access protection remains;
UI verification used a kubectl tunnel to the deployed container.

Code: branch `codex/hub-project-inventory` in my-prefect-server (both remotes).
Operational documentation: `docs/project-inventory.md` on that branch. The branch
is deployed but has not been merged into main, so deployments from main need to
retain this pin until integration.

## Correction: restore the public/private boundary

The user rejected redirecting public Evergreen into private Hub. Evergreen
commit `2cb7647` restores the public app and costs page, and live verification
confirmed services.json (46 checks), Worker /status (46 passing), and costs load
without authentication. Hub was rolled back to its exact prior image `1d6985f`
(Kubernetes revision 25), with its existing presence configuration intact.
The branch's web source and Hub manifest were also restored to that revision.

Fleet remains active every 15 minutes, with its expanded 50-endpoint coverage.
Commit `0926394` additionally checks that Evergreen serves the public app and
valid JSON inventory: HTTP 200 with a meta redirect or login page now fails the
contract. Tests cover this regression. Discord fleet links point back to public
Evergreen. Safe data sharing between the sites remains unimplemented; do not
reintroduce the rejected UI consolidation.

# Remaining HeavyPad migration

Nate approved continuing the previously excluded long publishers, Gardener and
Typeahead on October 27 after the earlier 21-deployment goal completed. Retain
Dagster unchanged; defer its asset-story cleanup. PDS Records, Dep Bump and Pi PR
remain approved retirements. This continuation ends with routing readiness
assessment; canonical hostname switching and Hetzner deletion are not included.

## Long publishers

`pub-search-snapshot` (original `4ef90900-2e9f-4579-a2ad-b620424ea479`)
and `leaflet-atlas` (original `eb95a7d8-0103-499a-bfe8-983ad4580127`)
move to the one-slot `heavypad-build-pool`, isolated from monitoring and analytics.
Prepared target clocks are inactive. Preserve cadences `40 */2 * * *` and
`0 */6 * * *` UTC respectively. The original definitions have empty schedules.
Before qualification, fence original clocks and cancel queued old runs, verify
no original active run, then register the target definitions at a published pin.
Enable target clocks only after real output qualification.

Leaflet retains Python 3.13.11 and one-thread ML settings. Command prefixes enable
the secrets plugin and preserve shared storage despite worker setting precedence.
Its existing history database is
`/home/stoat/prefect-analytics/pub-search-atlas/history.sqlite3`: 1,722,195,968 bytes,
read-only `PRAGMA quick_check` returned `ok` before migration. Preserve it and
snapshot it before the first new writer. Pub Search retains its persistent
`/home/stoat/pub-search-snapshot` root, production R2 bucket/channel, promotion
gates and upload cap. rclone exists in the worker's `~/.local/bin` PATH.
At preflight neither original publisher was active; the build worker was active
with MemoryHigh 24 GiB and MemoryMax 32 GiB. Host available memory was 46 GiB
and root free space 941 GiB. These are snapshots, not capacity reservations.

All ten required Secret block names exist in canonical SOPS; only the already
migrated Cloudflare/Anthropic names are present on the target. Extend the scoped
secret verifier and compare/sync values without displaying them. No issuer
credential rotation is included.

## Remaining cohorts

Gardener: autofix, autofix-revise, investigate, merge-approved, phi-pull-review,
pi-agent, test-pull-patch, watch-tangled-pulls; preserve approval semantics, pinned
runtime images/wheels, event chains, public Hub callers, inference bridge and
comment bridge. Do not generate a real proposal/merge merely as a smoke test.

Typeahead: identity-hourly, enrich-backfill, index, plc-identity. Preserve the
separate cloud builder pool and its worker, publication state, budget limits and
paused PLC cadence. Do not move cloud builds onto the home monitoring worker.

Live Hetzner has six recurring clocks: the two publishers, watch-tangled-pulls,
typeahead-identity-hourly, typeahead-enrich-backfill, typeahead-index. Other needed
workflows are manual or event-driven; clock inventory alone is not a retirement
checklist. The public Hub/phone ingress still runs on Hetzner, and background
services and other repositories still contain original Prefect clients.

## Rollback

Drain target writers before switching ownership. Disable target clocks and
trigger rules, reconcile their queued runs, and restore each original definition
and only its previous cadence/actions from revision
`1880ea0ef79d023ae4e334376f1bb4a60b12d221`. Retain all production state;
never restore an old data snapshot over newer writes. Never enable both owners.
Record qualification runs, exact output receipts and caller checks below.

## Publisher qualification in progress

Committed source `0df3a36e6e409de8bf9071199b5707e070bdcd31` registered target
Pub Search `04d53dd8-8567-47dc-bce3-d3c7bca0ae9c` and Leaflet
`f51119ee-9c9d-4bb4-8a47-6eb4c75bb3b8`, pinned before any run. Original clocks
were cleared and original Scheduled/Pending/Running/Paused/Cancelling inventory
returned zero. Canonical and original values matched for all ten credentials;
the selected target blocks were synced and read back without displaying values.
Qualification runs: Pub Search `532d5c9b-d743-4bdb-932e-d4c20bbf6d92` and
Leaflet `792691b2-7a1f-4346-8ec9-957cf7ac9e31`. Both target clocks remain inactive.
The private Leaflet history backup is
`~/.local/state/prefect-home/migration-backups/pub-search-atlas-20261027.sqlite3`,
SHA-256 `b82c6815e8e2f5230888cb02c7e6ffc7e7b48d499679a495091614028da6b8b2`;
SQLite backup and `quick_check` passed before the new writer.

## Gardener private transport

The old VMs call the public Hetzner API. Existing pinned Gardener images already
include stock `ts-unplug`; the approved per-run userspace transport plan was not
wired into the production worker. `mps.exe_tailnet` prepares a supervised proxy
on each owned VM before its engine starts, using a single-use, preauthorized,
ten-minute ephemeral `tag:gardener-vm` key minted by the existing tag-scoped
OAuth client. The OAuth credential remains on HeavyPad. Only the per-run key
travels privately on SSH stdin into a root-only environment file. The worker
sets `PREFECT_API_URL=http://localhost:18080/api`; the proxy forwards to
HeavyPad's private TCP listener at `100.96.216.23:14200`. No public API listener
is added. The small live-policy edit adds only that tag-to-port grant and tests;
existing grants remain during handoff. Stop/remove per-attempt enrollment on
terminal outcomes/cancellation; retained VM proxy service also has a runtime
limit. Ephemeral node removal after going offline remains Tailscale-managed.

The source still needs live VM qualification before switching Gardener callers.
`client.env` and `gardener-tailnet.json` are scoped canonical SOPS derivatives,
separate from the original worker environment; their renderer compares hashes.
Rotation procedure: edit the existing canonical values, apply the renderer,
drain/restart the corresponding clients and prove authenticated execution.
No issuer credential is rotated by this migration.

## Private transport qualification

Disposable real VM `gardener-tailnet-probe-7d1ac481`, using the unchanged pinned
investigate image, enrolled through stock ts-unplug and reached the HeavyPad
API over the tag-scoped private listener. Anonymous deployment-list POST returned
401; authenticated POST returned 200 and all 34 then-present deployments. The
VM and supervised proxy were stopped/deleted afterward. The HeavyPad listener
is the unprivileged `prefect-home-tailnet-api` user service, bound specifically
to `100.96.216.23:14200`, forwarding raw TCP to `127.0.0.1:14200` (including
WebSocket traffic). Its process uses about 9 MiB. Existing Serve/Funnel listeners
were unchanged. The live policy now contains the additional narrow grant and
accept/deny tests; the original grant is retained during handoff.

Concurrent upstream Typeahead changes `e45a792` and `79be9aa` were merged before
further rollout. The prepared target preserves refresh image
`sha256:34c5a61a794674e64802d5d633dd800a2adfab2c0d91f0d8ceb14e5c28a84da3`
and the unknown-freshness rejection. A real original Typeahead refresh run was
active during preparation, so the cloud worker must not be switched until it
finishes. Preserve its live results and current served generation.

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

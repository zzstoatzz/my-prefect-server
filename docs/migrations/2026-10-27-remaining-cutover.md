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

## Cohort qualification and caller handoff

Six target runs completed: investigation `06967b7d-9a90-4e51-9d97-d41bb5fa4a4a`
returned `migration-check-ok` through the real exe.dev/Aperture path; its trusted
engine reached the private API and the worker cleaned up the VM. Local Pi
`537c231f-0803-4789-ba4a-6161237e4afd` also completed. Autofix
`d47393d5-1e0e-41ff-9d1b-163b095b9b9b` completed `DryRun` (gather only; no
proposal). Phi review `72410975-77e5-47d3-a9be-b18473a033e0` completed
`Validated`, with no review request sent. Small real Typeahead updates completed:
identity `ad6f61b7-af7c-4aba-92ec-775c8e41bdde` and profile enrichment
`a52d9386-3ece-4ca4-9138-aaf3bb27fe9b` (limit three each).

Original Gardener execution automations are fenced. Ten retained Gardener/merge
events and `autofix_handled_comments` transferred transactionally without replay;
the comment subscriber is stopped during the checkpoint/caller handoff. The
new exe.dev worker is installed from `fa8bbac` with the same immutable flow images,
new investigation/revision deployment IDs, and private per-run transport. The
retired Pi PR route is omitted. Dedicated two-slot Gardener and Typeahead home
workers are active, each capped at 4 GiB; the cloud builder remains separate.

The Hub requires read-only access to the new API. Reuse the existing canonical
`prefect.api_readonly_auth_string`, add it to the HeavyPad server's derived
Secret, and roll only API/services settings; retain the admin credential and
all state. `GARDENER_API_URL` switches only the dashboard. Presence routing and
its credential stay as previously qualified. Check read-only queries succeed
and write/Secret reads remain rejected before switching the dashboard.

## Completed publication and guarded runtime checks

Pub Search qualification completed at 03:22:06 UTC after publishing production
build `b1793070306-141f`: 152,156 documents, 860,479,488-byte replica, SHA-256
`21d3153f070d73543552499e03be0ebfd1d14b47bee65e28d439c36b5afb75c0`.
Canonical bucket readback of `latest.json` matched its production build ID,
size, checksum and source revision `d581aa57b33ac3a3cdee627e5e2838213cfb97a6`.
The public search health endpoint returned ok; this alone does not identify the
backend's adopted build. The builder performed its original integrity/count
checks before pointer publication.

Test image qualification `9eec4780-9a47-4721-a423-d80feb751263` completed the real
filesystem/UID/environment/DNS sandbox boundary checks through the new API.
Revision guard `1a2c269c-d64d-41cf-9914-84e1e2fa1309` and merge guard
`3ef1c107-cc5e-4ef3-abc0-3fb46f85732a` completed Skipped for a deliberately
untrusted non-pull identifier. No proposal, approval or merge was fabricated.
The comment subscriber resumed its retained cursor and returned health 200;
its inference/workflow caller is active on HeavyPad with the retired proposal
route omitted. The first HeavyPad watch reconciliation
`b220b08b-beb7-47c2-bf32-e11e7028cc6c` completed normally.

The canonical read-only credential was installed on the HeavyPad API and services
without changing their image. Real checks: deployment POST filter 200; write
and Secret read 401. Hub image `77cefbd` built from a clean committed checkout
and rolled out with the scoped `GARDENER_API_URL`; browser access still presents
the Cloudflare Access login gate. Authenticated dashboard visual verification
requires an existing Access session; no gate was bypassed.

Enable the qualified Pub Search, watch-tangled-pulls, identity-hourly and
profile-enrichment cadences on HeavyPad only. Leaflet, the cloud index publisher
and the paused PLC definition stay inactive until their remaining checks pass.

## CI ownership and current validation

MPS CI now registers `prefect.heavypad.yaml` against the HeavyPad API, rather
than applying original-server rollback definitions through a hostname that will
later move. Its existing two spindle rows are explicit HeavyPad references in
canonical SOPS with `_inject_prefect=false`; the global original credential and
Stream's scoped rows are preserved. Scoped apply and two readbacks were current.
No pending/running MPS CI jobs remained when changing ownership. The preceding
CI failures stopped at formatting a concurrent Typeahead controller edit; the
format-only correction is included here. No failed CI registered deployments.

The full local suite passed 829 tests with seven skipped in 284.38 seconds;
Ruff checks, formatting, types and both deployment contracts passed. The Hub
check/lint passed with zero Svelte diagnostics. From the actual new Hub pod,
read-only requests returned the HeavyPad exe pool, ONLINE worker and expected
investigation/revision/test deployments. Public dashboard visual verification
is still limited by the Access login gate. Dagster remains active and unchanged.

The restore checker now requires all fourteen remaining deployment IDs and the
manual test-boundary canary in addition to the earlier migrated records. Deploy
that checker, take a final encrypted offsite backup and restore it after the
last publisher qualification and worker handoff.

## Approval and dashboard links

The frozen merge runtime and dashboard components still contained canonical
old-server links. New run/artifact IDs would not exist there before hostname
cutover. The flow and dashboard now derive their Prefect UI base from the selected
backend. The frozen merge wheel is patched with
`scripts/patch_merge_ui_wheel.py`, guarded by original SHA-256
`15dcb69ab74c54e1dee674f0600bdc1a7211a8966cbfe7f93d2747f6492106c6`.
Only `flows/merge_approved.py` and its wheel RECORD change; all other archive
members are byte-identical. Output wheel SHA-256 is
`d20c42618e0269a64db514a019e21bbf775211dd4db2aa29ed5ffec001e91a7c`.
Approval conditions and merge behavior are untouched; the patch honors the
existing target `PREFECT_UI_URL`. The original wheel remains available.
Fourteen merge tests passed; frontend checks/lint passed after link changes.

The first new-target CI attempt failed before executing user steps with build-agent
connection timeout, not a deployment registration failure. Retry the current
revision and retain an honest CI result in the completion audit.

The native Typeahead release chat owns the current controller and will provide
an explicit release-complete handoff. A terminal retry/failure alone is not a
worker migration gate: retain its prepared candidate and allow that chat to
finish resumable qualification. Do not start another controller concurrently.

## Final Leaflet qualification and CI credential correction

Leaflet qualification `792691b2-7a1f-4346-8ec9-957cf7ac9e31` completed at
04:01:54 UTC and published https://cccc98da.leaflet-search-ujy.pages.dev.
The map and summaries are fresh (643 clusters, 253 regions). The optional facts
step hit a read timeout and retried before publication; its final task result
is recorded separately rather than treating the first retry as a flow failure.
The archive grew to 1,740,005,376 bytes, preserving prior history. Activate the
original six-hour cadence on HeavyPad only; the original clock stays empty.

The subsequent CI checks passed but registration discovered the job still received
the original API URL. A malformed SOPS index had created three unintended
reference keys rather than changing the nested MPS entry. Those exact keys were
removed, all unrelated decrypted values were compared unchanged, and the intended
nested entry was updated and read back. Two existing MPS spindle rows changed;
subsequent MPS and Stream checks reported zero drift. The scoped renderer now
asserts the resolved URL/auth belong to HeavyPad, rather than merely accepting
whatever the store happened to contain. CI also asserts its resolved Prefect
settings and reads the existing HeavyPad pool before registration. A negative
original-server target check failed before making any API request.

The original home worker is stopped and disabled after its cohort drained;
only the separately owned original cloud rollout remains active. The actual
restore checker is installed and its 46 required deployment IDs all exist among
47 live target records.

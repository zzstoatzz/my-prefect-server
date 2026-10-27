# Remaining HeavyPad migration

Nate approved continuing the previously excluded long publishers, Gardener and
Typeahead on October 27 after the earlier 21-deployment goal completed. Retain
Dagster unchanged; defer its asset-story cleanup. PDS Records, Dep Bump and Pi PR
remain approved retirements. This continuation ends with routing readiness
assessment; canonical hostname switching and Hetzner deletion are not included.

## Current checkpoint

The remaining cohort has fourteen definitions. Its original clocks are empty;
five of its six recurring clocks are active on HeavyPad. The cloud index worker is ONLINE
on HeavyPad. Its real 1 GiB qualification passed; the sixth clock activation
and final backup/audit receipts are being applied below. PLC remains intentionally
paused with its cache preserved. The original home worker is disabled; Gardener
callers, execution rules, state and notifications use the target backend.
Dagster remains active and unchanged. Canonical routing is unchanged and is
**not ready for cutover or node deletion**; see the assessment below.

[The generated audit](../audits/2026-10-27-remaining-migration-verification.json)
records live IDs, clocks, qualification modes and pending gates. Earlier sections
below preserve chronological preparation/qualification receipts; their inactive
clock statements describe those checkpoints, not current ownership.

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
step timed out on both attempts; publication used the committed facts fallback.
Do not treat those facts as freshly regenerated.
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


## Latest backup and CI network path

The 04:13:54 UTC offsite restore passed using `20261027T041342Z.dump.age`
(SHA-256 `2a19e505379b9a1713466293f5e6b869a40cd1f2a5d2dd240675c3369a6ea678`):
47 deployments, 426 runs, 43 blocks, 23 automations and 27 tables; restore took
three seconds. All 46 required deployment IDs were present. Repeat after the
cloud worker handoff.

CI revision a884039 passed checks but private hostname resolution failed. The
follow-up direct private IP probe failed with `Network is unreachable`.
Spindle's guest networking intentionally rejects private/special-purpose ranges
and filters their DNS answers; this is an isolation boundary, not a DNS bug.
Neither the boundary nor the public API exposure is changed.

Registration therefore runs on the trusted HeavyPad host. The user service
`prefect-home-registration.service` checks every five minutes, requires a green
`deploy.yml` push pipeline for the exact current GitHub main SHA, checks out that
SHA in its own clean clone, verifies the existing private HeavyPad pool, validates
the release and registers `prefect.heavypad.yaml` with `MPS_PIN=@<SHA>`. A lock
prevents overlap; the successful SHA receipt makes repeats a no-op. Failed or
pending CI cannot authorize registration. The CI workflow keeps its full suite
and frontend checks, without trying to cross the runner's private network boundary.
Canonical scoped CI credentials retain the HTTPS native HeavyPad target; no
credential rotation occurred. Host service qualification and CI result follow.

## Canonical routing readiness assessment

**Not ready to switch routing or delete Hetzner.** Workflow ownership and node
retirement are separate gates. Canonical DNS remains `prefect-server.waow.tech`
→ `167.233.79.205` (TTL 300); this continuation changes neither it nor TLS.

Before a canonical API hostname switch:

- Complete the separately owned Typeahead native release, receive its explicit
  handoff, retarget the drained cloud worker, qualify it on HeavyPad, enable the
  new index clock and repeat the final backup/restore audit. A terminal failed
  release run is not permission to take its worker or launch another controller.
- Serve the canonical hostname with a valid certificate on the intended private
  HeavyPad listener; verify from the laptop, phone, old cluster and CI guest.
  The native HeavyPad certificate currently serves a different hostname.
- Resolve remaining old-API consumers: the self-hosted Prefect MCP service,
  bandwidth watchdog and its event automations, chaos timer, Phi's hosted
  read-only clients, operator defaults and mobile API/push clients. The watchdog
  still loads `%h/.config/prod-worker/env`; chaos loads its separate environment.
  Audit their effective targets and preserve each service's intended behavior.
  Do not silently retarget the chaos job along with a hostname.
- Preserve the `/push` service on the canonical hostname: its APNS configuration,
  source ConfigMap and persistent state still live in the original cluster.
  Test phone push/presence and direct API access before moving that route.
- Adjust the off-tailnet public health probes for the private endpoint and verify
  one scheduler/execution owner, notification links and rollback with both APIs
  reachable. Keep original credentials/data and rollback routes until accepted.

Before deleting the original node, also relocate or explicitly retire services
that are not moved by transferring Prefect deployments: the Typeahead cloud
builder and its large volume, Hub, presence/push, public spindle and Grafana.
HeavyPad's orchestration migration does not transfer those compute/storage
resources. Dagster stays on HeavyPad, unchanged.

The safe next routing action is a separately scoped hostname/client cutover,
with `/push` and mobile verification included. Node deletion requires its own
remaining-services inventory and data disposition afterward.


## Cloud worker handoff received

At 04:29 UTC the Typeahead release owner explicitly released the drained worker
for migration while retaining ownership of its native candidate and next run.
This supersedes the earlier wait-for-release-complete sequencing: terminal
failure alone did not authorize the change; the explicit handoff did.
The same root systemd worker switched to the private HeavyPad API at 04:30 UTC;
configuration applied once, then two checks were byte-current (environment hash
`6120f71529980efb7d1c9eb39232e10d9ef098d17d92daae3fd7763a557a38e9`).
HeavyPad reports `ProcessWorker 86a4ce11-5bd8-499d-aa58-c74decad3695` ONLINE in
`typeahead-cloud-pool`, heartbeat 04:30:29.983419 UTC. Deployment
`a0c0ce9c-5bab-4bdc-8512-39984c322a74` remains inactive. The release owner received
that handoff and owns its next qualification run. No competing controller,
Fly candidate modification, serving-route switch or volume transfer occurred.

The native cleanup blocker was a missing `COLLATE NOCASE`, which prevented the
handle index from being used: corrected query returned 72 actors in 2.4 seconds,
versus the original 60-second timeout. The release owner is testing/publishing
that correction and will provide the successor immutable image before the
active publisher pin/source is updated. Its successful HeavyPad run and index
clock activation remain the final workload gates.


CI revision `1506d7ae8600bc683fb2b3396115df3b6cffe4b3` passed (827 tests,
six skipped; Svelte/lint zero errors/warnings). The real host registration
completed successfully against HeavyPad; a second service run reported the same
SHA already current without registering again. The pending-CI guard had already
refused the same revision before CI succeeded. No runner isolation change was
required.

Typeahead successor image from native revision
`f67005bf6c1c304790199e17bc8253332dddcb05` is
`registry.fly.io/typeahead-search-refresh@sha256:b0492f07c8237d94c854da8530b3af5eab905506e69ff0104fa1ba5d98449bcd`.
The publisher's HeavyPad definition and original rollback source use that exact
image. The release owner retains exclusive control of the inactive candidate
and next qualification run; the schedule remains inactive pending that result.


## 04:39 UTC waiting checkpoint

Revision c8e7d03179c20ffbd1d02911dbb52209a9939963 passed CI and was registered
successfully by the trusted HeavyPad host service. The index clock remains off.
The live audit is incomplete only for the index qualification and clock.

The offsite restore at 04:39:25 UTC passed using `20261027T043911Z.dump.age`,
SHA-256 `7e21d88c212fd1866d59f71e51d75a41f42b911a0a53b590ede464aeaacdde75`:
47 deployments, 440 runs, 43 blocks, 23 automations, 27 tables, three-second
restore, newest-completion gap two minutes. This is a waiting checkpoint;
repeat after final index qualification/clock activation.

The release owner measured 12,000 updated rows in 24 successful pages since
04:34:31, zero query errors, approximately 58 rows/second. Candidate restart
requires replay from its snapshot watermark (about 167,000 rows on the prior
pass), despite retaining admitted data and overlay. At 04:38 UTC the estimate
was 45–65 more minutes, roughly 05:25–05:45 UTC, including cleanup and
qualification. Do not forge readiness or launch a second controller to avoid
that wait. The release owner will send measured checkpoints and its HeavyPad
qualification UUID. Goal stays incomplete; no canonical routing or node deletion.


Because CI no longer registers deployments, its two scoped Prefect credentials
are being retired. `stream_spindle_prefect.py --repo my-prefect-server` now
requires no canonical CI URL/auth and removes only those two existing rows;
`_inject_prefect=false` prevents global-original credentials from being restored.
Stream and the trusted host's separate canonical client environment are preserved.
Readback and registration-health verification are required after retirement.


## CI credential retirement verified

Revision `31026c05932327abb0f5e1e3cad46a1761d52180` passed CI and the trusted
host registered it successfully after the unused CI credentials were retired.
Only the two canonical MPS CI references and their two existing spindle rows
were removed. Check-only mode first detected both stale rows; apply removed two;
two subsequent MPS checks and Stream's separate check reported zero drift. The
canonical trusted-host client and enrollment files remained current by hash;
a real HeavyPad pool read returned its expected process pool. No credential
rotation or publisher cadence change occurred. The next newly started CI job
will also exercise validation without those retired environment values.

## Home traffic observation

At approximately 04:46 UTC existing Prometheus physical-interface counters
(`enp.*|wlp.*`, instance `heavypad`) reported 11.02 GB received and 2.15 GB sent
in two hours; 29.88 GB received and 4.57 GB sent in six hours. These are total
host interface bytes, including other services, CI/dependency downloads and LAN
traffic, not exact ISP billing or per-task attribution.

The Pub Search qualification uploaded an uncompressed 860,479,488-byte snapshot
to R2 and read the full Turso corpus inbound (separate wire bytes not recorded).
Its existing two-hour cadence implies approximately 10.3 GB/day of snapshot
uploads at this size, before source reads. Leaflet exported vectors from
Turbopuffer and uploaded seven changed Pages files while reusing 53; exact wire
bytes are not available. Both publishers already executed on HeavyPad under
the old worker, so orchestration migration did not relocate their steady-state
compute; qualification added one real extra publication of each. The checkpoint
encrypted PostgreSQL dump was 8,541,413 bytes. No new bulk Typeahead/55 GiB PLC
replay ran at home; its worker compute and volume remain in the cloud.

The Typeahead release thread was given these measured totals and attribution
limits in response to Nate's bandwidth question. No large transfer was started
to measure them, and no publisher schedule was altered in response.


## First scheduled Pub Search run

Automatic HeavyPad run `cad7990d-5e38-4e21-82e0-16369cb8a87b` started at
04:40:06 UTC and completed at 04:58:51 UTC. It published production build
`b1793076097-77aa` with 152,182 documents; snapshot size 860,635,136 bytes,
SHA-256 `8eb262d39b3ce06f99faa448b5c66a7f8c864a4c1c38a3dd0e38c0367750b278`.
This independently verifies automatic schedule ownership after manual
qualification. Leaflet's next six-hour scheduled run is later; its real manual
publication remains the qualification receipt.


## Clean successor replay

The release owner reports a clean pass at 05:36:07 UTC: 171,641 updates,
133 clearances, 17,897 applied deletions (17,916 returned, 19 skipped), no errors.
The corrected clearance query completed in about two seconds, and scoped
`selfhosted.social` search returned actors. The persistent policy marker is the
guard for cached sizing restarts. The release owner is comparing inactive
2CPU/1GiB, 2CPU/2GiB and 2CPU/1GiB with a temporarily frozen source; it owns
restoring original environment/connectivity and selecting any intentional size
change before launching the HeavyPad controller. The parent does not probe the
candidate during that experiment or enable its publisher clock.


## User hold and scope correction

Nate challenged the scope drift into Typeahead performance work and said
"hold on". Migration work is held after returning to the original 2CPU/1GiB
policy. The optional 2GiB controller/docs change was landed in3034641, then
reverted and pushed as `e263f796697b8be2c17cc6ad1e793582b683bf50`.
The live publisher runtime now pins that one-GiB source; image b049 is preserved
and the index clock remains inactive. This is not a completed migration receipt.

The only attempted cloud qualification was
`c303fd74-bdcb-40d9-a113-5ff9b8ecd285`: started05:58:05 UTC, forcibly CANCELLED
at05:58:40 UTC. Logs reached source checkout and flow start; no qualification
success is claimed. The cloud worker was stopped/killed to prevent further
controller activity, then restored active on the HeavyPad API after the safe
runtime pin was restored. New worker
`ProcessWorker6489084e-d724-417a-b1d5-ac85c40a695c` was ONLINE at06:01:44 UTC.
The retained Fly candidate was observed started at2CPU/1GiB and public freshness
was healthy (116s lag). The release owner is independently checking the routing
record/ETag; no routing outcome is inferred solely from flow cancellation.

The RAM comparison completed with normal Turso connectivity and verified quiet
windows after the attempted URL-freeze was aborted by its isolation guard.
Across three arms, all2016 response hashes matched, and no sync occurred during
workload. Warm p99 was31.024/16.393/34.388ms at1/2/1GiB; first-touch p99 remained
roughly117/131/120ms. This does not establish cause of the historical outage or
fully reproduce live multi-second spikes. That code/performance investigation
belongs to the separate Typeahead release thread and must not expand this
Prefect migration. Remaining migration gates stay index qualification, clock
activation, final audit/backup and the already-documented routing assessment.


## Final migration qualification

Nate explicitly resumed migration with "get it migrated!! stop hedging!" and
asked that other-session messaging stop. Native performance work is separate.
The successful HeavyPad qualification is
`3234d469-2b31-4b5e-8513-b9bce417daed`, started06:04:12 UTC and COMPLETED
06:07:52 UTC. Runtime was published e263f796697b8be2c17cc6ad1e793582b683bf50,
with the existing b049 image and original two shared CPUs / 1 GiB. All five
public verification rounds passed. Artifact cleanup retained both current
compact and legacy builds; zero objects deleted. Local archival had zero
outstanding generations and pruned zero bytes. Source checkout/execution and
post-publication checks ran on the existing cloud worker, using HeavyPad API.

The transferred index cron remains `0 6 */2 * *` UTC. Activation follows that
success. Today's06:00 slot elapsed during qualification, so one idempotent
regular catch-up run is queued after the successful controller drains; it uses
the normal deployment defaults, not another qualification/recovery run. This
preserves the intended slot rather than silently waiting until October29.
Canonical Prefect DNS and Hetzner node retirement remain outside this change;
Dagster is unchanged. PLC stays deliberately paused.

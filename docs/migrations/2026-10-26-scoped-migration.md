# Scoped HeavyPad migration

Nate approved batches 1, 2, 4, 5 and 8: 21 deployments. Long publishers,
Gardener/coding, Typeahead, canonical routing, node deletion and broad source
reorganization are outside this goal. Preserve existing unrelated checkout work.

| Batch | Deployments | Status |
| --- | --- | --- |
| 1 | phi-curation, phi-likes-review, phi-editorial, phi-character-retro, phi-chicken-scout, phi-chicken-precheck | Migrated: six validation runs passed, all target schedules active, original clocks empty. |
| 2 | watch-fastmcp, fastmcp-brief | Migrated: both real runs passed, checkpoints/history transferred, clocks and automations active. |
| 4 | strata-hourly, phi-tag-maintenance, phi-atlas, docket | Migrated: all four passed real execution/publication, clocks and docket completion trigger transferred. |
| 5 | ingest, classify-emails, transform, brief, phi-memory-synthesis | Real ingestion, classification, Dagster build, briefing and memory synthesis passed on HeavyPad; clocks and completion chain transferred. |
| 8 | email-backlog, email-triage, phone-presence, stream-admission | Migrated: email read/preparation and public phone ingress passed; stream CI submitted and completed the full build gate. |

## Phi qualification

Source `eaa4680cd97e60ef27254db2dfbc1b90d7d780ca` adds `validate_only` to the
existing phi-trigger flow. It reads live bot health and sends an authenticated
unknown-slot request. The bot checks its token before returning its supported
slots and cannot schedule a known bot pass on this route. Each target slot must
be advertised. Validation completes with state type COMPLETED/name Validated;
it proves deployment execution, token loading, network, health and API contract,
not completion of an editorial/market pass. Real scheduled behavior remains due
on its original cadence; no weekly/monthly/deadline pass was replayed for testing.

| Slot | Original deployment | HeavyPad deployment | Completed validation run |
| --- | --- | --- | --- |
| curation | ca4af159-74b1-495d-b226-86ea82f5015b | af3781c0-020f-45eb-90de-801aca6e9786 | c6d4191c-7bcc-4c3d-8843-d13349bd99cf |
| likes-review | 1b210402-2c7e-474c-9340-9eb732c29425 | ef81bea3-f5f6-4e9e-8d02-616571fdfdd1 | 504d6fca-0399-447b-8ef6-4e58d5da3b9d |
| editorial | 49461499-3c20-422b-aba1-5f3d9a8955b1 | 7056695e-b9b4-4729-b1d4-5b67a1fdb3b2 | 15721422-6b84-4dce-9405-417d82181830 |
| character-retro | ec2b334a-5292-476b-aa8e-d58fa7b68752 | cabf0900-4a3f-4137-ae8c-df5bacc46e5d | fee6ffa9-fd55-4bf6-8ddc-1403cbdfff14 |
| chicken-scout | ec556fa6-c4e3-4684-8d92-6ac7c69e26af | 9bc72ce6-5adb-43c5-87ed-9e6a48329176 | 7476f14e-1ced-4460-9bbc-49bb4fc92aa7 |
| chicken-precheck | c6200903-8f8a-41a0-8492-612464c78256 | 3b13d70f-bd6f-458f-925a-d7329db1149a | 7558dd1f-9824-4bf1-94a6-4fc8b1330029 |

The original schedules were emptied in source `58b46ac` and explicitly cleared
live. Readback found six empty schedule lists and zero old Scheduled, Pending,
Running, Paused or Cancelling runs for these six deployment IDs. HeavyPad
activation source is `68359e03a26e341fc1e1749571078429e36d7f35`, retaining the
original UTC cron expressions and adding per-deployment concurrency one/CANCEL_NEW.
The known email-triage paused run remains untouched.

The Phi token matches canonical SOPS and original Prefect. Target drift was
introduced while its schedules were inactive, detected, and repaired. Rotation
remains manual/unperformed; the secret-store README describes the issuer and
consumer sequence. Existing HeavyPad failure notifications and target run links
were verified with no synthetic Discord message. Automatic Gardener diagnosis
remains on the original server, as before; its migration is outside this goal.
No Grafana alerting was added.

Rollback: clear selected HeavyPad schedules, cancel unstarted queued runs and
wait for active execution to finish. Restore only those original source crons,
register the selected original deployment(s), and verify one owner before resume.
Keep history and the shared token; never replay missed market slots as catch-up.

Atlas's first natural run `6471ce1e-ddb2-4ee2-9b56-fe682f74629e` completed
October 27 at 00:45:10.635151 UTC, closing the prior batch's pending observation.

Live target readback confirmed all six active UTC cron schedules and future
automatic runs with ordinary slot parameters (no `validate_only` override).
The next chicken precheck is run `93f5fccb-5f43-4237-9038-6196bee8ff09` at
October 27 04:00 UTC. No scheduled pass has fired yet after this cutover.

## FastMCP pair

Original deployments: watch-fastmcp `e6102704-a550-49e3-bfc6-ac1e9d15816f`,
fastmcp-brief `a2e05fd4-12b9-4388-80c8-c6365cd88258`. HeavyPad targets:
watch-fastmcp `ad73bd44-c4b6-4c83-9db3-3b4294952e43`,
fastmcp-brief `0391974c-686d-4332-a051-efb1eb9dc55a`.

Both target definitions were initially registered inactive from `2674e1e`.
Original schedules were emptied in source and cleared live; both original
brief-trigger automations were disabled in source and live. The transfer script
refused writes unless both clocks were fenced, both deployments had no queued
or active runs on either server, and the original trigger automations were off.
It transferred `fastmcp_notifications_last_modified`, `fastmcp_briefed_threads`,
and the complete six-hour window (24 GitHub events), preserving event IDs,
resources, related resources and payloads. History entered PostgreSQL directly
in a transaction, without sending it back through the broker or replaying
notification automations. API readback verified event content and both Variables.
No plaintext credential entered that history or script.

`just heavypad-prefect-monitoring --fastmcp --apply` remaps both briefing
automations to the HeavyPad deployment, enables them only after original
automations are fenced, and copies/verifies the existing brief delivery rule.
The normal invocation still checks the original three monitoring rules. The
notification block matches by hash; no synthetic Discord message was sent.
The existing original brief-delivery rule remains for historical/other producers.
Both checkpoint consumers and the HeavyPad Anthropic block are canonical.

Real watcher run `8a8a30d5-9a87-4914-9a1c-04e2c1e979aa` completed at
01:16:50 UTC October 27. Brief run `d626c086-84cc-4148-abc9-916b7126f2e7`
completed after reading the transferred history. These are real normal runs,
not validation-only executions. Inspect their logs/results for whether anything
new needed delivery; completion alone does not assert that a message was sent.
The original five-minute watcher and four-hour briefing UTC cadences are retained.

Rollback: fence both HeavyPad schedules and event-trigger automations, drain
active runs, transfer the now-current checkpoints and trailing event window back
without replay, then restore original schedules and original briefing automations.
Do not resume with the stale original checkpoint snapshot.

FastMCP publication proof: the first HeavyPad brief read 17 threads, found six
unbriefed versions, enriched one still-open issue and emitted a one-item brief.
Notification automation `f5c4dfe5-4236-4262-8ccd-de5233e3ea78` recorded
`prefect.automation.action.executed` event `11ef272a-eb2b-49ba-ae3b-dda9e217cfc4`
at 01:17:05.377397 UTC, linked to the real brief event
`01a17aee-ac69-7ac7-84d3-4cd346007a53`. This qualifies actual Discord action
execution without a synthetic message. The first watcher used the retained
watermark and received GitHub HTTP 304. Activation source is
`d82fcd2eb45550605bad3842b59f26173fc42758`; target schedules were read back
active and original schedules empty, with zero remaining original live/queued
runs for this pair. The first natural watcher run is
`8c5e1c68-a269-4599-93d5-fbad27ad69b2`, due 01:20 UTC October 27.

R2 restore `20261027T011848Z.dump.age` passed at 01:18:56 UTC, with all 19
target deployments, 259 runs, 15 blocks and six automations present. Ciphertext
SHA-256 `58857a92d52d437821f7f47948b6921370170e4329f54f569c9c28edb10951b5`.
The deployment ID checker now requires the eight deployments in batches 1 and 2.
Local full suite passed 828 tests, seven skipped, plus Ruff/format/type checks.

## Email approval disposition

Nate explicitly instructed cancelling the old triage approval. The cancellation
command on `aa1e067f-2127-4575-a412-fa8b0f56092c` found it already terminal;
immediate API readback confirmed type CANCELLED/name Cancelled and message
"Flow run was cancelled successfully." No approval input was submitted and
no triage decisions were replayed. This removes the pending approval blocker;
the email deployment, worker capacity and callers still need migration.

## Stateful and interactive handoff

Original clocks for strata, tag maintenance, Phi atlas, ingest and triage were
cleared in source (`70f3472`) and live. Five original completion automations
(classify, transform, brief, memory synthesis, docket) were disabled in source
and live. No old Running/Pending/Paused/Cancelling runs remained at transfer.
The previously paused email approval stayed Cancelled.

HeavyPad has three additional systemd worker instances from `84aeb58` and
`worker@.service`: background (one slot, 8 GiB), build (one slot, 32 GiB), and
interactive (two slots, 2 GiB). Monitoring retains its separate one-slot worker.
The interactive override and pool both allow two slots so an approval wait does
not block mailbox review. All units are enabled at boot; resource limits are
per-service, not guaranteed reservations. Actual background memory accounting
showed about 0.6 GiB anonymous/5.5 GiB reclaimable file cache and zero OOM kills
during the memory-synthesis qualification. Build and background work can run
independently; the original worker remains for excluded workloads.

The new `analytics-duckdb-writer` limit is
`43131bae-b0be-4ce9-bc4d-0f6048cae013`, one slot. Dagster was idle before
both services received `dagster-prefect.conf` and restarted. Live process
environments confirmed both target the HeavyPad API. All transferred readers
and writers retain `/home/stoat/prefect-analytics/analytics.duckdb`, append-only
spend logs and existing archive/result paths. The private pre-cutover DuckDB
copy SHA-256 is `4547af3dc4620488a8392681aed8b437ab4a248116633ea5d4cfe09c08885fd7`.
The 69,402,624-byte Strata SQLite backup passed quick_check. Neither backup
is a license to overwrite subsequent production writes during rollback.

| Deployment | HeavyPad ID | Qualification run |
| --- | --- | --- |
| strata-hourly | 25b8694f-c057-4c1c-8555-2357bf47d2f0 | 0687d7a7-68db-4034-80cf-60d789d3c877 |
| phi-tag-maintenance | fab797b2-e506-4651-9935-4ea2c7255917 | 3494c436-0035-45d9-8401-cd174cc4d576 |
| phi-atlas | b35e93ef-44a9-40eb-917d-66711669d485 | dry run c3e6030f-1691-4a96-98e6-c2cf38deb7d9; publication 547d5309-3352-4648-9d61-ad2263da37d3 in progress |
| docket | 7242289f-3822-4073-8acb-1673062c78ef | completion-triggered qualification pending |
| ingest | f5959783-a226-4734-abb7-66096c305649 | 02e049e7-a875-43c5-8119-543bde89896d |
| classify-emails | 9e5a00f0-751d-46b9-93b2-8a9c224879c0 | 91e9a3a1-f30d-41d2-9ded-193093831501 |
| transform | 94738309-b61e-43fa-8713-b610c2604a63 | 9c86ac51-939c-4ab7-8588-ea09200b4f0b |
| brief | c004d049-278a-40d5-bfd0-1de1b2d5dfc8 | ece5248e-3c56-44b5-a6fe-19952d55ffcb |
| phi-memory-synthesis | a3d785e9-a88f-421d-837f-81e4eb7f535d | 940d5575-5e3b-4983-ae7b-d8e34c94cdcc |
| email-backlog | b3f1e43e-451c-4587-bb47-eaf63f45e777 | 1a2bf8aa-39bc-4493-b917-f52de2c86b37 |
| email-triage | f952bb35-d8eb-4a81-a0a5-94fb4f0b08eb | a0c967a6-a3f0-473f-9a97-17098e7f4018 |
| phone-presence | d4ec52ed-6085-4d94-b56f-27a7713e2336 | public ingress be1537a8-f130-4bb0-a6b7-1dd8e4dc9aac |
| stream-admission | 583bb4f9-89b2-4c8c-8436-5fd5c11ac5a5 | 3592a7e7-b697-4df7-9b0d-49338c702b63 in progress |

Strata ingested 353 of 353 changed/new segments and published ten changed
content-addressed objects with no D1 writes, using the existing checkpoint
and filesystem lock. Tag maintenance stored 141 relationships. The Atlas dry
run computed 10,221 points and 8 coarse/329 fine clusters, without publication;
normal publication and docket must still finish before that sub-batch is complete.

Ingest completion triggered classification on HeavyPad. The first transform
failed before loading its flow because `path` plus `pull: []` did not establish
the working directory for its installed Prefect runner. `94f4d39` adds an explicit
set_working_directory pull step for `/home/stoat/dagster-hub/current`. The retry
above passed; the matching Dagster run is SUCCESS with 16 asset materializations.
It triggered briefing and memory synthesis on HeavyPad. Memory synthesis completed
at 02:02:39 UTC October 27: all 127 users processed, 41 liked-post observations
extracted, eight actionable, and one superseded row. No code/workflow replacement
of Dagster was performed. Its existing installed release remains in place.

`b9d0523` corrects another Prefect runner environment precedence issue: worker
PREFECT_LOCAL_STORAGE_PATH overrides a deployment job environment. The first
new memory pass populated 127 cache files in the worker's new results directory
while the original shared directory held 7,552. Explicit command-prefix env now
preserves the declared shared result path after the worker merge, like the earlier
plugin fix. This retains cached material for future runs. Verification of the
updated runtime must accompany completion; source configuration alone is insufficient.

The email-backlog probe first supplied an invalid body_chars=0 and failed its
existing validation before reading mail. The corrected one-message/one-character
read above completed on the interactive worker. The earlier queued corrected
probe `e946fd33-25af-4452-860d-4b44c7beff1d` was cancelled before moving queues.
Triage `validate_only=true` prepared two shortlist items and the correct private
HeavyPad approval link, then completed Validated without emitting a shortlist,
pausing, submitting instructions or writing decisions. Seven local triage tests
passed. `just triage-reply` now addresses HeavyPad; no test reply was submitted.

### Phone ingress

The phone's public URL and bearer credential remain the same. Its new scoped
PRESENCE_PREFECT_* settings target HeavyPad; Gardener's PREFECT_API_URL remains
`http://prefect-server.prefect.svc:4200/api`. Hub image `28df734` was built from
a clean committed clone and deployed without unrelated local checkout changes.
The backend renderer `just heavypad-presence-ingress --apply` derives API auth
from canonical SOPS and the target deployment by name, without changing the
original ingress/Gardener secret. Apply/check are idempotent.

The Hetzner host resolves HeavyPad's shared Serve/Funnel hostname to public
Funnel IPs; its private 10443 listener is unreachable there. A Hub-only hostAliases
entry now resolves that name to the private tailnet IP. TLS health returned 200
from the actual Hub pod afterward. No Tailscale access policy was changed; the
checked-in policy copy was refreshed verbatim from the live API.

The new presence queue was paused during routing changes. After rollout and
old-run drain it was resumed. The public endpoint rejected anonymous reports
with 401 and accepted an authenticated 1970 stale report with 202; the recorded
HeavyPad run above completed on the presence queue. Its timestamp cannot update
current presence or actuate lights. The private credential files, writer limit
and lighting marker are reused, and source/package revision `0706dd46` is preserved.
A real arrival/departure was not simulated. `just presence-deploy` now registers
the HeavyPad definition; the separate original deployment file is rollback material.

### Stream caller

Stream commit `923f2eae50a49c98b47fdff26ec45849e7ac2d09` resolves its deployment
by name instead of hardcoding an old UUID. Its existing spindle entries now
reference HeavyPad API/auth; `_inject_prefect` is false for this repository only.
The original global Prefect credentials and other spindle entries are preserved.
`uv run scripts/stream_spindle_prefect.py --apply` repairs only the two routing
consumers; two subsequent checks reported zero changes. A real Nixery container
reached the private API. Four CA contract tests passed. Actual spindle workflow
`admission.yml` succeeded on pipeline `3myt472pd6622`, submitting the tracked
HeavyPad run with all 21 suites, no skips and build_image=true. That build must
be observed terminal and its outcome/receipt checked before final qualification.
No stream production service or routing was deployed. Preserve unrelated
`tests/seq_reuse_repro.py` in the stream checkout.

## Completion audit

All 21 approved deployments passed the live ownership audit in
[the verification snapshot](../audits/2026-10-27-scoped-migration-verification.json).
It reads both live APIs, compares all target clocks and per-deployment concurrency
limits/strategies with source, checks each qualification run belongs to its
expected deployment and is Completed, asserts zero original Scheduled/Pending/
Running/Paused/Cancelling runs across the scope, checks seven automation
ownership transfers, notification enablement, both one-slot writer limits,
worker readiness and the resumed presence queue. This is a bounded operational
snapshot, not a claim that future runs cannot fail.

Final source activation `797b1d7` enables the Phi atlas clock and preserves
its daily 13:00 UTC cadence. All thirteen recurring clocks in the scope are
active on HeavyPad; five completion-triggered and three manual deployments
retain those execution modes. Original schedule lists are empty. The six bot
clocks were qualified without replaying off-cadence bot passes; their next
normal execution remains on the retained cadence. Triage's daily clock is
active, while validation created no new pending approval.

Phi Atlas publication `547d5309-3352-4648-9d61-ad2263da37d3` completed at
02:10:08 UTC October 27. Public PDS readback returned 10,226 points, a
9,721,044-byte blob and record CID
`bafyreiah57upcke6em33uxekkaab453omyesmqkuennfozjfdm4cwxform`. Its
completion automatically created docket run
`01b1be8a-6c55-48db-a699-6d742f046231`, which completed at 02:18:52 UTC.
Public docket CID `bafyreigczztjuxkevxhny5tolategtauncc5pjcv6z3mwfm7xryhjefyku`
has ten candidates and references that exact Atlas CID. A read in the installed
Dagster environment, holding the HeavyPad analytics writer lease, verified the
matching ten-candidate DuckDB history row. The existing archive remains usable
by the installed analytics runtime after the new writer's publication.

The next natural analytics cycle created memory run
`929f5f9e-ff1b-4fe8-b8ee-2e80908120d3` and completed successfully. Its actual
engine environment read `/home/stoat/prefect-analytics/storage` from the
command-prefix override, and task records showed 125 Cached summaries and two
new completions out of 127. This verifies cache preservation through the real
worker path rather than merely checking job metadata.

The native stream run completed at 02:11:29 UTC. Its receipt at
`/home/stoat/stream-gate/receipts/923f2ea.json` records all 21 suites pass and
image ID `sha256:9dc641700985af7b8bb73d20977ad73bed2e77ffede89dbfeb43c069445bbd6c`.
The image was built locally; its registry digest is null because no publish or
stream service deployment was requested. A negative check passed a local image ID
to `admit verify`, which rejected it: that command requires a published registry
digest matched by a receipt. The existing publication gate remains in force. The migration moved the gate's caller
and execution ownership, not the stream service.

The final Python suite passed 828 tests with seven skipped. Ruff, formatting,
types and both deployment contracts passed; the Hub check/lint and eight
presence tests passed. Notification rules and their copied Discord block were
rechecked, as were canonical ingress, spindle and server/worker consumers.
The Logfire disk warning, critical and missing-sample rules were active and
evaluating without matches/errors at 02:21 UTC. No Grafana alerting was added.

R2 restore `20261027T020740Z.dump.age` passed at 02:07:50 UTC with all 32
HeavyPad deployments, 297 runs, 23 blocks and 13 automations present. Its
ciphertext SHA-256 is
`70aa6cb100035ade2ebdfc7d578dcdee0a6f5dcc56e6a423df9c36cdcb002529`.
The deployed restore checker requires all 21 scoped deployment IDs as well as
the previously migrated workload records. The final post-publication R2 backup
`20261027T022939Z.dump.age` restored successfully at 02:29:48 UTC, with all 32
deployments, 330 runs, 23 blocks and 13 automations. Its ciphertext SHA-256 is
`6da052843446416063f8daa41c2834788d09aeb161f318d4ad84dea124a617bd`; the
newest completion was four minutes before the dump. No required qualification
or migration work remains in this scope.

## Coordinated rollback

Drain the selected HeavyPad cohort and fence its schedules, callers and event
automations before restoring the old owner. Keep the current data, external
publications, checkpoints and shared caches: restoring a pre-migration DuckDB
copy would discard later production writes. Transfer current FastMCP Variables
and its trailing event window back without replay before restoring its clocks.
For analytics, stop/drain both orchestrators' writers and Dagster runs first;
remove the two Dagster Prefect environment drop-ins, restart the idle services
on the old API and only then restore the original clocks/completion triggers.
The one writer lease must live on one server for all active writers.

For phone rollback, pause the HeavyPad presence queue, remove only the three
scoped PRESENCE_PREFECT_* environment entries from Hub and wait for rollout;
its preserved original ingress configuration becomes active. Drain any queued
new observations before allowing old/new processing; keep observed timestamps
and the existing lighting marker. For stream rollback, fence the new gate,
restore only stream's canonical spindle routing to the old API and sync those
two consumers, then verify name resolution and one gate owner. Never bypass
the gate or publish an unqualified image. No old history or credentials were
deleted.

Canonical routing, retiring Hetzner, the long publishers, Gardener/coding and
Typeahead remain outside the completed goal. The public phone ingress and
Gardener dashboard still run on Hetzner; only the phone's Prefect backend moved.
The node remains required until those separate retirement gates are resolved.

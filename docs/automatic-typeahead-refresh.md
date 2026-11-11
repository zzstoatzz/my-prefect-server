# Automatic Typeahead refresh

Typeahead search is served only by the Tantivy slots `typeahead-tantivy-a` and
`typeahead-tantivy-b`. The `typeahead-index` deployment in `prefect.heavypad.yaml`
refreshes the inactive slot from a fresh cloud checkpoint and promotes it behind
the empirical release proof. The [slot lifecycle](tantivy-slot-lifecycle.md)
describes preparation, promotion, retirement and the journal in detail. The native
engine was removed on 2026-11-02; its history is in the
[archived native refresh record](archive/typeahead-native-refresh.md).

## Deployment

The deployment runs on the `typeahead-ephemeral` pool at 06:00 and 18:00 UTC with
`automatic_refresh: true`. Each run gets a four-hour worker bound, which leaves room for one flow retry. The flow takes
three optional parameters:

| Parameters | Effect |
| --- | --- |
| none | Publish one isolated Tantivy generation to R2 without touching routing. |
| `automatic_refresh: true` | Publish, prepare the inactive slot, promote, then prune. While a store slot serves: a [standby refresh](#standby-refresh-while-a-store-slot-serves). |
| `automatic_refresh: true, resume_rollout: true` | Finish a pending automatic preparation; never start a build. |
| `tantivy_operation: {...}` | Run one explicit `prepare`, `cancel` or `promote` slot operation. |

`tantivy_operation` cannot be combined with the other two, and `resume_rollout`
requires `automatic_refresh`. Never save `resume_rollout` or a slot operation into
the scheduled defaults.

Every mode clones the exact `INDEX_TANTIVY_SOURCE_REVISION` and loads the
controller's installer from that checkout, checksum-pinned by
`INDEX_TANTIVY_ADAPTER_SHA256`. The automatic mode also requires
`INDEX_TANTIVY_RELEASE_RECEIPT_KEY` and `INDEX_TANTIVY_RELEASE_RECEIPT_SHA256`
before any checkout. Publication additionally pins `INDEX_TANTIVY_BINARY_SHA256`,
`TA_RETRIEVAL_STRATEGY` and `INDEX_PDS_FALLBACK_SHA256`. Fly access is the two
app-scoped `FLY_TANTIVY_A_TOKEN` and `FLY_TANTIVY_B_TOKEN` Secret blocks.
`INDEX_BUILD_ROOT` and `INDEX_MIN_REMAIN_GIB` bound the disk reserve of streamed
subprocesses.

## Offline import performance

Publisher `26fae38` selects importer `f1e6e3a2…` for bulk import only, while keeping
reader `ef2be085…`, the qualified Fly image and every admission gate. The importer
uses libc-backed normalization arenas instead of allocating OS pages per call.
The production reader performs the full-build source handoff and freezing checks;
the release records both executable hashes. Worker count, memory, commit batches
and final segment count retain their existing defaults. The matching source
selection is declared in `prefect.heavypad.yaml`. It was registered November 5,
2026 at 17:54 UTC; the [readback](audits/2026-11-05-typeahead-fast-importer-registration.json)
confirms source pins, one-run concurrency and the unchanged 06:00/18:00 schedule.
Full production timing remains pending the next scheduled build.

The controlled million-actor comparison measured 129.25 seconds for both baseline
runs and 52.73 seconds for the allocator change, with identical search results and
peak memory. This is an import result, not a measured full production refresh.
[Evidence and artifact identities](https://tangled.org/zzstoatzz.io/typeahead/tree/main/docs/evals/refresh-performance-20261105)
also record a compatibility check using the actual production reader.

## Source poller lifetime

The qualified November 5 continuous-poller image keeps the source process alive
across its durable hourly byte-budget windows. The preceding image rechecked a
1.5 GB immutable PDS mapping on each hourly process restart, pausing source reads
for roughly 88 seconds and repeatedly failing the reader's 120-second readiness
limit. The new image retains the reader, installer, admission checks and source
byte limits. Managed inactive-slot installation and cached restart passed;
guarded promotion and observation of the first hourly boundary are tracked in the
[Typeahead release record](https://tangled.org/zzstoatzz.io/typeahead/tree/main/docs/ops/poller-readiness-20261104).

## Lifecycle release

The fast-install configuration qualified November 6 pins Typeahead
`bcd0e08`, installer `5214936`, and release proof
`control/tantivy-releases/8619968fed901ea40a299d7a9a4fc0fda3bb0838b63a2074d66284632a132306.json`. Its immutable image is
`registry.fly.io/typeahead-tantivy-a@sha256:979785c393e162f1256c709eef0a2f969a85357d427f44a93d0f2f298b551841`. The image replaces only the downloader and installer; reader,
live-source modules, final allocation and admission gates retain their qualified
identities. Transfers use resumable parallel ranges; archive verification runs
with extraction while per-file checks remain mandatory. The existing snapshot
was reused for qualification, avoiding another corpus export/import.
[Qualification and deployment evidence](https://tangled.org/zzstoatzz.io/typeahead/tree/main/docs/evals/refresh-performance-20261105/)
distinguishes the configured pins from verified production promotion and registration.
Automatic continuation inherits the image from the serving admission. The
publisher implementation remains pinned to `26fae38`; schedule and concurrency
remain 06:00/18:00 UTC, one run, `CANCEL_NEW`.

### Prior lifecycle qualification

Initially registered November 5, 2026 at 11:10 UTC with publisher `0a36b8b`
and verified again at 15:05 UTC with publisher `4f1b9b7`: Typeahead
`7d73263`, installer `cf893cb`, and release proof
`control/tantivy-releases/7a89f411cbc09bd791fe4241238154acf2df1ddce3c1eca3f7ddd6239871c759.json`.
The proof binds image
`registry.fly.io/typeahead-tantivy-a@sha256:d13dee4839c3e56ac1f6dd4f0d14cb4514186221b0f5a6d3037cfb92f090473d`.
Automatic continuation inherits that image from the serving admission. The
[registration readback](audits/2026-11-05-typeahead-lifecycle-registration.json)
verifies exact pins and unchanged schedule, parameters and concurrency. The
[qualification and cutover](https://tangled.org/zzstoatzz.io/typeahead/tree/main/docs/ops/lifecycle-cleanup-20261105/)
records all six release gates, matched full-data comparison, 93 cloud checks
without skips, public aliases/PDS scope and canonical ranking.

B serves the new runtime. It reports checkpoint coverage staleness without
stopping a healthy reader, still rejects stale admission, and sends source
stdout/stderr to Fly logs instead of accumulating a volume log. The previous A
rollback retains its older expiry/provenance behavior. This runtime cutover reused
the existing checkpoint and did not renew coverage. A normal 18:00 scheduled full
refresh and obsolete-object consumer audit remain required before completion.

The live Prefect API also excludes `retry_type=in_process` from worker polling.
A bounded cloud retry used the same VM and PID across its 90-second delay; both
polling paths excluded it. See the [API rollout audit](audits/2026-11-05-in-process-retry-rollout.md).
Deployment concurrency remains one with `CANCEL_NEW`; production timeouts and the
worker were unchanged.

Automatic journals retain `publication_execution_id` separately from the current
lease holder. A retry of the flow that published the generation finishes as
ordinary `Completed`; a separate recovery, or an older journal without that
publication owner, finishes as `RolloutResumed`. Explicit slot operations and
publication-only runs use `SlotOperationCompleted` and `GenerationPublished`.
Only full-refresh completion resets the existing 13-hour missed-refresh clock.
These distinctions passed real Prefect-engine cloud tests; actual manual
prepare and promote emitted the named event. The normal scheduled full-refresh
event still needs verification.

### Cleanup retries after promotion

A retry after successful promotion and pending-marker removal previously entered
publication/preparation again. Stage caching kept export/import work, but the
same artifact could be installed into the other slot unnecessarily. The new
controller first checks the admitted slot's durable journal. Only a healthy,
already-promoted automatic operation whose immutable publication owner is this
same execution can reuse the completed promotion. The existing plan, release,
route/ETag, qualification and public canary checks still run. A different execution
still starts a fresh full refresh; changed routing still fences transaction
recovery. The guard is registered at `4f1b9b7`, verified 15:05 UTC. All 93 local and
Linux cloud checks passed without skips, source and configuration main CI passed,
and commit-by-commit checks passed. The [registration readback](audits/2026-11-05-typeahead-cleanup-retry-registration.json)
confirms unchanged schedule, parameters, concurrency, Typeahead source, installer
and runtime release proof. Only the publisher source pin and command changed.
The normal scheduled 18:00 full refresh remains pending; no extra build or
production failure was induced for this qualification.

## Publication

Publication runs as separate Prefect tasks. `export_checkpoint` takes one Turso
checkpoint and retries twice on failure. `import_checkpoint` smoke-imports a
sample and imports the full checkpoint with live catch-up. `freeze_checkpoint`
freezes the bundle. Each task persists its result to the
`typeahead-index-results` S3Bucket block (R2, under
`prefect-results/typeahead-index/`) with a run-scoped cache, and the flow
retries once after five minutes. A retry on the same VM therefore resumes after
the last finished stage instead of downloading the checkpoint again. The flow
saves that block at start from the R2 credentials the run already resolved, so
it holds no value the secret store does not. Typeahead's
`scripts/snapshot_compaction/publish.py --engine tantivy` uploads it under
`releases/search-tantivy/<build>/` with full readback and writes the manifest
last. It changes no pointer. Only after readback are the local index and tar
retired.

New publication and installation do not require native-manifest provenance.
The retained older A rollback still validates its existing anchor in
`releases/search/`; keep those objects until a verified full refresh replaces
that consumer and the retirement audit permits deletion.

## Rollout and recovery

The automatic mode refuses a route whose `current` is not a Tantivy slot. It probes
the serving slot three times, 20 seconds apart. A dead slot does not block
preparation: the corpus floor then comes from the admitted serving artifact in R2.
A pending automatic journal is resumed before any new export. A route or ETag
change during publication or preparation aborts the run and keeps the immutable
publication.

A failed candidate stays in the inactive slot. Run
`just prefect deployment run typeahead-index/typeahead-index -p automatic_refresh=true -p resume_rollout=true`
to finish it without another export, or cancel it with a `tantivy_operation`
`cancel` request for the same `request_id`. There is no routing rollback to
another engine. If public verification fails after promotion, the controller
restores the prior route only while its own write is still current.

## Standby refresh while a store slot serves

Typeahead's `scripts/snapshot_refresh/store_slot_route.py` can route search to
the store slot `typeahead-shadow-a`. It writes `{current: store slot, previous:
the managed slot that was serving}`, and its rollback returns to `previous`.
`STORE_SLOT_ORIGINS` in `typeahead_refresh.py` names that origin. While the
record's `current` is one of them:

- `Refresh.route()` raises `StoreSlotRouted`, so the promoting refresh, artifact
  cleanup and every `tantivy_operation` stop at their first read of the record,
  before any export, publication or Fly call. `_put_route` refuses a store slot
  as `current`, and a store slot app is refused as a mutation or Fly target even
  if it is added to the slot lists.
- The scheduled `automatic_refresh` run becomes a standby refresh
  (`typeahead_tantivy_standby.py`). It exports, builds and publishes as usual,
  retires the managed slot that is not the rollback target, installs and
  qualifies the new generation there twice, checks the release proof, and then
  replaces only `previous` with one ETag-conditional write. `current` is copied
  byte for byte and the run refuses a record it cannot copy that way. The next
  run rebuilds the other slot, so each slot is rebuilt once a day and the
  rollback target is never the one being replaced.
- The slot lifecycle runs against the rollback route, the record a rollback
  restores: `{current: rollback target, previous: the other slot}`. Before the
  first standby refresh that is the `prior_route` in the store slot's admission
  under `control/store-slot-admissions/`. Each standby refresh stores the next
  one under `control/tantivy-standby/<new rollback target's admission digest>.json`
  before it updates the record. `store_slot_route.py rollback` must read that
  object once `previous` no longer equals the admission's prior current.
- The inactive-mutation lease is held in the run, not in the record: a lease in
  the record would block the rollback tool for the length of a preparation. The
  pending-candidate object and its journal fence a second run. Any failure
  leaves the record as it was; the next run resumes the pending candidate.
- Guards check the rollback target's health, not the public aliases, which
  answer from the store slot. A rollback target that is down does not block the
  run, as with a dead serving slot.
- Cleanup keeps both managed generations, pending ones and the store slot's
  bootstrap build.

A standby refresh that built and recorded finishes as ordinary `Completed`, so
the missed-refresh automation keeps its 13-hour clock on the rollback slot and
its retry is another standby refresh. After a rollback the record again names a
managed slot and the next run is a promoting refresh. If a rollback lands while
a standby candidate is still being prepared, that run stops, and the next
promoting refresh resumes and promotes the same candidate. If it lands after
the candidate was prepared but before `previous` was written, promotion refuses
the stale plan: cancel that `request_id` with a `tantivy_operation`, then run
the refresh.

## Retention

After a confirmed promotion and public verification, cleanup deletes unrouted
generations under `releases/search-tantivy/`. It keeps the current, previous and
pending generations and is fenced while a mutation lease exists or a routed
generation lacks its publication. It never deletes anything under
`releases/search/`, so legacy objects remain until a separate consumer audit retires them. This
controller never treats that prefix as Tantivy retention.

## Full-refresh success signal

Registered publisher (November 5, 2026): the missed-refresh automation
expects `prefect.flow-run.Completed` for this deployment. Only a normal automatic
refresh that builds, publishes, promotes and completes cleanup uses that default
success state. Manual slot operations finish as `SlotOperationCompleted`,
publication-only runs as `GenerationPublished`, and recovery of an existing
rollout as `RolloutResumed`. A standby refresh that builds, installs and records
the rollback target also uses the default state. All retain Prefect's `COMPLETED` state type and their
original result, so callers must check the state type rather than its name.
Those operations do not reset the full-refresh retry clock. A failed operation
still fails normally. The normal scheduled full-refresh event remains an outstanding production
verification; manual operation events have been verified live.

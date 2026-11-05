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
| `automatic_refresh: true` | Publish, prepare the inactive slot, promote, then prune. |
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

Registered November 5, 2026 at 11:10 UTC: publisher `0a36b8b`, Typeahead
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
rollout as `RolloutResumed`. All retain Prefect's `COMPLETED` state type and their
original result, so callers must check the state type rather than its name.
Those operations do not reset the full-refresh retry clock. A failed operation
still fails normally. The normal scheduled full-refresh event remains an outstanding production
verification; manual operation events have been verified live.

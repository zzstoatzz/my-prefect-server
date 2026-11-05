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

The configured release proof is
`control/tantivy-releases/2cb383df761467c5d7afcb374e9baa01e040faf35170d78b518ac655d3e6b4ba.json`,
bound to image
`registry.fly.io/typeahead-tantivy-b@sha256:bc66ce9eec3aca5d568135e6d0637f2e2be6e0c305095fc29e4fd50c15d0d34d`.
Automatic continuation inherits its image from the serving admission; this proof
must match that image after cutover. The publisher's source, binary, mapping,
06:00/18:00 UTC schedule and automatic-refresh parameters are unchanged.
Reusing the current artifact for this image release does not renew checkpoint
coverage; the next scheduled full refresh remains necessary.

### Pending lifecycle transition

The controller source removes legacy native-manifest provenance and can replace
a reachable slot whose checkpoint coverage has expired. Those changes are not
registered in production: the deployment retains its immutable staged-publisher
pin and compatible installer until the new runtime passes release qualification.
The staged export/import/freeze tasks and persisted retry results are preserved;
freezing now returns only the publication path, without a native-anchor directory.

The combined runtime and checkpoint helpers passed 36 real-I/O fixture tests on
disposable cloud compute, including restart past coverage expiry while stale
admission remains rejected. That does not establish full-data performance or a
completed refresh. Follow the [qualification record](https://tangled.org/zzstoatzz.io/typeahead/tree/main/docs/ops/lifecycle-cleanup-20261105)
before changing pins. Update the publication and retention sections below when
the provenance-free runtime and publisher are both verified live.

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

The fixed slot image refuses a checkpoint artifact unless it carries a
`rollback_native_anchor` whose manifest bytes it can fetch from
`releases/search/<anchor build>/manifest.json`. Each build copies that anchor from
the serving Tantivy artifact's manifest in R2, and the pinned installer checks the
bytes against the recorded SHA256. No native backend is read.

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
`releases/search/`, so the anchor manifests the slot image fetches remain. Older
native objects in that prefix are no longer managed by the controller.

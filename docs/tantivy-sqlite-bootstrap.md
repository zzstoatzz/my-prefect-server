# Tantivy checkpoint publication

The Tantivy branch of `typeahead-index` uses one full SQLite checkpoint, followed
by a canonical stream into the pinned reader and a genuine live-source catch-up.
It does not build a fresh native prefix index or run a second full source walk.
The existing native publication is an independent rollback anchor. Native
publication behavior is unchanged. Production serves Tantivy; the archive PDS
join below is a separate candidate and has not been cut over.

## Acquisition and import

`mps.typeahead_checkpoint.fetch_checkpoint` downloads one `/export/<generation>`
and contiguous `/sync` batches. A successful short batch defines a finite WAL
prefix; HTTP400/500 never counts as completion. SQLite verifies the exact
committed frame count before materializing a standalone database. The receipt
binds the source-origin hash, source clocks bracketing the transfer, generation,
base/WAL/materialized hashes, bytes and verified frame count. It makes no live
freshness or moderation claim.

Acquisition defaults to eight GiB of source bytes,64MiB of WAL and180 seconds,
with a maximum900-second application deadline. Progress reports only phase,
bytes, requests and elapsed time. Blocking I/O can overrun the application check;
the caller must also impose a hard flow/worker deadline. Failed bulk transfers
are not retried automatically. The cloud VM owner handles forced-exit cleanup.

`mps.typeahead_checkpoint_stream.stream_checkpoint` verifies the exact receipt,
materialized database hash and unchanged file witness, then reads one SQLite
transaction. Ordered actor rows merge with orphan tombstones; live actor rows
win over retained tombstones, and cleared handles become deleted documents.
The canonical adapter preserves profile fields, counts, f32 scores and labels.
There is one actor serialization pass, directly through a pipe into the reader;
there is no full JSONL staging file. Actor reads use a sequential table scan and
a file-backed binary-DID sort to avoid scattered table reads through the DID
index. The producer reserves twice the database size plus 64 MiB for sorting,
using temporary files in its owned workspace and SQLite’s 64-MiB soft heap limit
(which is not a hard process RSS cap). The reader validates
the footer hash and full input count. Raw rows, imported documents, live/deleted
documents and the retained native count remain distinct.

The reader retains the bootstrap receipt in its public source metadata. Only
moderation policy hash/counts are included; the rule arrays stay private. Fixture
helpers and small diagnostic samples retain explicit fixture/partial identities
and cannot qualify for production.

## Optional archive PDS join

`INDEX_PDS_FALLBACK_SHA256` pins a metadata-only SQLite mapping at
`releases/pds-fallback/<sha256>/pds-fallback.sqlite`. The cloud publisher fetches
at most 3 GiB, verifies size/checksum, and uses it for snapshot conversion and
live catch-up. Explicit path/checksum arguments support isolated cloud validation.
An unset pin retains source-only behavior; an invalid pin fails.

Only empty source PDS values are filled. Existing source PDS, cleared handles,
deletions and moderation retain authority. The imported manifest binds the
mapping identity and policy; the portable artifact includes it. The serving
reconciler requires that exact mapping so profile observations do not erase
joined values. Source-known, source-missing, joined and remaining-missing counts
are reported separately. Archive membership is partial, not fresh DID resolution.

Before approving the candidate, include its mapping pin in the publisher
configuration; otherwise a later source-only refresh would drop joined coverage.
Candidate preparation does not change production pins.

## Live handoff and serving

The replay floor precedes the provider's first source-clock observation by an
explicit300-second overlap. A verified full import records its footer hash and
initial counts. An exact cursor/revision CAS then initializes the real remote-v2
client at that floor. Admission requires an actually completed remote discovery
cycle covering the checkpoint cut and a fresh, complete post-cut moderation
observation. Local download/import timestamps never stand in for live coverage.

External admission dispatches on the distinct checkpoint source kind; it does
not relabel the reader's traditional `cutover_source_ready` flag. Checkpoint
coverage expires after24 hours unless a genuine complete remote round renews
it. The slot supervisor stops both reader and reconciler before expiry and
refuses an expired cached restart. Ordinary timestamp polling cannot renew it.

`mps.typeahead_checkpoint_release.run_cloud_release` is cloud-only. It downloads
once, smoke-tests a100,000-document sample, imports the full database, catches up
live state and freezes an immutable bundle. The existing publisher verifies the
bundle and independent native anchor. No routing pointer changes in this path.
The preparation task is limited to 90 minutes and requires a separate hard worker
timeout. Serving qualification, recovery and cutover approval remain separate.

## Evidence and remaining qualification

Real HTTP/SQLite/WAL tests exercise committed replay, malformed/partial responses,
byte and deadline limits, slow trickling responses, provenance and no-overwrite.
Actual pinned Linux-reader tests cover full stream import, HTTP/restart retention,
policy privacy, live cursor handoff, freeze/install and coverage expiry. These
small fixtures do not establish full-cloud build time or serving readiness.

The October 30 archive-join candidate reuses one verified cloud checkpoint for
full import and qualification. Its isolated receipt must establish actual PDS
coverage, build/storage cost and runtime behavior before promotion. Older
source-only production qualification does not establish those new properties.

A completed actor stream can outlive the original build deadline during the final
segment merge. `build_checkpoint(..., resume_import_from=previous_workspace)`
requires identical checkpoint, source, native-anchor and mapping inputs. It moves
the existing index into a new owned workspace and uses `resume-import` with the
checksum-bound stream receipt. The Rust finalizer requires a committed verified
footer, matching manifest/counts/hash, and no live-source observations. Partial
streams cannot be resumed this way. Normal handoff and admission checks still run.
The larger build bound includes download, sample, full import and merge; it is a
failure cutoff, not a latency claim. The worker's independent two-hour bound remains.

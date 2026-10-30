# Tantivy checkpoint publication

The Tantivy branch of `typeahead-index` uses one full SQLite checkpoint, followed
by a canonical stream into the pinned reader and a genuine live-source catch-up.
It does not build a fresh native prefix index or run a second full source walk.
The existing native publication is an independent rollback anchor. Native
publication behavior is unchanged; Tantivy automatic rollout remains disabled.

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
there is no full JSONL staging file or second corpus copy. The reader validates
the footer hash and full input count. Raw rows, imported documents, live/deleted
documents and the retained native count remain distinct.

The reader retains the bootstrap receipt in its public source metadata. Only
moderation policy hash/counts are included; the rule arrays stay private. Fixture
helpers and small diagnostic samples retain explicit fixture/partial identities
and cannot qualify for production.

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
The preparation task is limited to 60 minutes and requires a separate hard worker
timeout. Serving qualification, recovery and cutover approval remain separate.

## Evidence and remaining qualification

Real HTTP/SQLite/WAL tests exercise committed replay, malformed/partial responses,
byte and deadline limits, slow trickling responses, provenance and no-overwrite.
Actual pinned Linux-reader tests cover full stream import, HTTP/restart retention,
policy privacy, live cursor handoff, freeze/install and coverage expiry. These
small fixtures do not establish full-cloud build time or serving readiness.

The first cloud diagnostic reached source export but failed its180-second
acquisition deadline before import. Provider readback confirmed its VM was
deleted; its temporary deployment was retired. The revised path adds transfer
progress and reuses the same checkpoint for sample and full import. Full-cloud
publication and actual serving-slot qualification remain outstanding until their
receipts exist. Production has not cut over.

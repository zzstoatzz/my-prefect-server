# SQLite bootstrap pilot

The proposed initial seed reuses the full SQLite export already needed by the
native builder. Fetch it once with verified WAL completion, then pass its path to
native `reuse_source` and stream it directly to Tantivy. There is no second export,
full JSONL staging copy or corpus-wide Hrana row walk. The existing native builder
otherwise deletes `source.db` before its expensive prefix-index phase.

`mps.typeahead_sqlite_seed.checkpoint_fixture` is an offline fixture helper only.
It reads one SQLite transaction, merges ordered actor rows with orphan tombstones,
preserves the canonical FullActor payload and f32 conversion, and emits the current
Rust import envelope. Existing live rows override retained tombstones. Cleared
handles become deleted documents; hidden profiles remain present so a later show
policy has a profile to expose. Complete `mod_overrides`/`blocked_domains` tables
are retained separately, with existing 10,000-entry caps and no invented policy
observation time.

The 32-actor fixture keeps actual committed changes only in SQLite WAL. Main-file
only reads retain the old values; the helper sees the new counts/score. A real Rust
import, HTTP search and authenticated source-state read pass. Partial fixture
admission is explicit; `cutover_source_ready` remains false. The 100,000-document
synthetic pilot then measured 1.182 seconds for scan/export and 4.509 seconds for import
on the local macOS host using the prebuilt cabfe5 binary. DB+WAL totaled 15,188,144
bytes; JSONL 41,389,733 bytes; committed index 9,000,699 bytes. This is a bounded
implementation pilot, not a cloud production timing estimate.

The fixture receipt is typed by explicit version/kind/coverage fields:
`schema_version:1`, `kind:sqlite-checkpoint`, `fixture:true`,
`coverage:checkpoint-only`, `live_coverage_verified:false`,
`completed_live_round:false`, `policy_freshness_attested:false`, plus file hashes,
input counts and stream footer hash. Supplied generation/frame coordinates are
fixture labels, not verified Turso storage attestations. Initial stored versions
remain the coarse import floor; original row timestamps survive in the input's
`source_updated_at` field, which the current Rust Actor parser does not persist.
Source-authoritative reconciliation already ignores timestamp-only differences.

Production integration remains blocked on these concrete contracts:

- Verify the exact exported generation and committed WAL boundary. The official
  [Turso CLI implementation](https://github.com/tursodatabase/turso-cli/pull/990/files)
  provides the existing path, but its first-sync error handling can return without
  fresh WAL. Success alone is insufficient. Hashing stable files or successfully
  opening SQLite also does not prove all expected WAL frames were applied.
- Persist and validate checkpoint bootstrap provenance in Rust/admission. The
  current importer ignores the extra `source_bootstrap` JSON object. It cannot
  substitute for the existing live reconciliation-round proof.
- Separate raw row/input/live/deleted counts from the native visible actor count.
  The current portable source validator equates native and imported counts;
  hidden/cleared/orphan rows make that equation invalid for a raw seed.
- Establish fresh source policy and a bounded storage-observation/catch-up cursor.
  `MAX(updated_at)`, local SQLite `unixepoch()` or download time cannot honestly
  populate a completed live round. Periodic backdated-change coverage also needs
  a real checkpoint observation, not a declared successful timestamp replay.

The next cloud pilot should use the same retained raw checkpoint, first 32 rows and
then at most 100,000 documents, with 128 MiB input/output bounds and a 180-second local
scan/import deadline. It must report checkpoint/WAL completion and catch-up
separately. If no retained raw checkpoint exists, measure and budget the one bulk
export before scheduling it; do not silently recreate the old hours-long path.
No cloud operation or production publication was performed by this fixture work.

The existing reconciliation algorithm was then exercised against the same readonly
32-actor SQLite/WAL transaction and actual Rust backend. Both source/backend DID
walks completed with 33 document hash comparisons: zero applied writes, 33 version-only
replays, unchanged stored document hashes, unchanged segment descriptors and
unchanged indexed memberships. It used seven local SQLite statements and zero
source network requests; the measured round took 0.068 seconds on the host. Its
cursor uses `sqlite-checkpoint-observations-v1` and an explicit checkpoint-only
coverage object with the immutable bootstrap receipt SHA. A separate-process
restart retains that distinction and stays cutover-unqualified. The ordinary
source client's policy validator rejects that cursor; it cannot silently relabel
its completed checkpoint markers as live Turso coverage.

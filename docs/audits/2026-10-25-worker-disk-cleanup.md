# HeavyPad disk cleanup and producer audit

October 25, 2026. Operator authorized cleanup and an audit of the producers.
Root disk went from 92% used / 148 GiB free to 43% used / roughly 1,000 GiB free.
Observed free space increased by 914,805,809,152 bytes (852 GiB). This is the
filesystem delta, not a sum of file lengths: benchmarks used many hard links,
so summing path sizes would substantially overstate reclaimed space.

## Retired payloads and preserved data

Removed 48 old `~/typeahead-index/build/build-*` directories. Kept the two newest
logical generations, `b1792141282-189b` and `b1792400507-4df8`, including existing
compact twins: the archive is now 36 GiB rather than 627 GiB.

Removed 184 database/prefix file paths of at least 100 MiB from these explicitly
owned, completed experiment roots: `typeahead-compaction-validation-1791480000`,
`typeahead-native-compaction-20261008`, `typeahead-pds-20261020`,
`typeahead-pds-coverage-20261020`, `typeahead-release-20261017`,
`typeahead-release-20261019`, and `typeahead-rollout-20261008`.
Scripts, manifests, logs and JSON evidence remain; each root has a retirement note.
Full-corpus reproduction now requires restoring the recorded source payloads;
old evidence paths are not claims that the payload remains present.

Before deletion, process cwd, open descriptors and mappings were checked. This
caught PID 192343, a localhost-only evaluation server orphaned since October 8,
with parent PID 1 and the exact executable under the old compaction lab. It was
stopped with SIGTERM and verified gone before cleanup continued. No `eval-server`
processes remained. The system `prefect-home-worker` stayed active.

No live serving disks, cloud publisher data, PLC bundles, Stream data, package
caches or active model downloads were removed. The independent model disk is a
separate filesystem. Production freshness and both search aliases passed afterward.

[Deletion summary](2026-10-25-worker-disk-cleanup.json). Full pre-deletion inode,
size, mtime and link-count inventory remains at
`~/.local/state/typeahead-retirement/cleanup-20261025.json` on HeavyPad.

## Producers and recurrence controls

| Producer | Diagnosis | Control |
| --- | --- | --- |
| Historical home snapshot publisher | Successful October 16 and 19 runs logged `serving build_id unknown ... pruning nothing`. HTTP 503 from freshness was treated as unreachable, even if a stale serving build could be identified. | Home publishing remains disabled. Pruning accepts an identified, working stale backend, preserves unknown identity, and fails visibly when unknown identity blocks an oversized archive. |
| Active cloud snapshot publisher | Pruning ran only after success; a failed prior attempt could leave retention deferred. Legacy/compact twins counted as separate retained builds. | Prune before new-build disk admission as well as after success. Retain two logical generations plus serving, with both formats. Missing archive is a no-op; symlink directories are excluded. Existing 96 GiB admission and 8 GiB runtime reserve remain. |
| One-off qualification campaigns | Full databases and hard-linked trial copies survived completed experiments; one evaluation subprocess outlived its launcher. These are not scheduled publishers. | Large obsolete payloads retired; no lab servers remain. Project instructions require a named scratch root, byte budget, process teardown on failure/cancellation, preserved compact evidence and payload cleanup before completion. |
| PLC bundle history | Incremental identity tooling uses this history; job remains paused. | Preserved; requires a separate checkpoint/recovery decision before deletion. |
| Stream tests and model/cache directories | Separate owners and, for model downloads, active work. | Preserved. This audit does not claim those directories are redundant or fully audited. |

The HTTP change is tested against a real local HTTP server: healthy 200,
identified-stale 503, broken 503, malformed identity and 401. Retention tests cover
real on-disk removal, twins, unknown identity and failure when retention is blocked.
No test merely treats an upstream error as authorization to delete an unknown build.

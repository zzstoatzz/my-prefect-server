# Typeahead cloud publisher recovery

Status: qualification running, October 25, 2026 UTC. Public freshness reports
an old base snapshot; live overlay lag and exact-handle serving remain healthy.
The index schedule was intentionally inactive during the October 20 home
bandwidth hold, and no replacement publisher had been enabled.

## Resources and source

The existing Hetzner orchestration host runs `prefect-typeahead-worker.service`
as `typeahead-indexer`, limited to one flow, one CPU and 3 GiB maximum memory,
with low CPU/I/O priority. Dedicated volume `107263158`, 150 GB, supplies
`/var/lib/typeahead-indexer/builds`. The old repair evidence remains untouched.
The API credential is a root-only EnvironmentFile; job secrets remain references
resolved by the existing MPS plugin. No credential values are in this document.

The source configuration and installer are on main and both remotes. The initial
installer ownership issue was fixed in `c92a0c9`; future flow commands also pin
Prefect 3.8.2 (`1d2041a`). The initial staging run started from MPS `0d402a8` and
resolved Prefect 3.9 for its run command; the dedicated worker itself uses 3.8.2.
That active run was preserved rather than restarting its work.

Pool `typeahead-cloud-pool`, ID `d3138cb1-9a45-49cf-8382-89d2e9c6dc3b`, is ready.
The named API route verifies it; the MCP pool-ID filter returned an unrelated
pool and must not be used as evidence for this pool.
Deployment `5d446811-c098-438e-b16c-f1ccd9da7e97` now selects this cloud pool.
Its schedule remains inactive, channel staging, production arming zero.
The builder tag remains `pds-compact-31ec142-20261020`, full corpus and PDS cutoff
10,000; no ranking or scope policy change is intended.

## Active run and remaining gates

[Staging run 39aa7b7b](https://prefect-server.waow.tech/runs/flow-run/39aa7b7b-cd58-4608-a117-84d9a7e35d2c)
started at 02:03:58 UTC. Compilation finished at 02:08:26. Source generation
1515677 is exporting to `build-b1792894106-51ec/source.db.part` on the volume.
The builder's heartbeat watches the final `source.db`, so its zero-byte report
during transfer is misleading; inspect the `.part` size for current progress.
Do not start a duplicate build or relay the corpus through the laptop/home.
The worker journal and Prefect run logs expose subsequent build phases.

The intended staging pointer is `staging/cloud-publisher-20261025/latest.json`.
Require the complete build, conversion and remote checksum readback, then native
admission/global/PDS queries and replay before updating production's snapshot
pointer. Preserve and verify the protected current recovery manifest and pair.
If successful, arm production publication and activate the cloud schedule in
`prefect.yaml`, land it on main, register and verify the next queued run. Do not
reactivate the home publisher or the independent PLC repair schedule.

The public freshness endpoint must return 200 with the new build before this
incident is considered resolved. Production's backend image and front-door
routing remain unchanged. Preparation of the new admitted-reader rollout follows
freshness recovery; executing that backend rollout requires Nate's explicit go-ahead.

## Storage correction and checkpoint reuse

The attached volume was unsuitable for active SQLite validation: after eight
minutes the native process had read only about 1.15 GB, with roughly 2 ms block
read latency. The first staging run was cancelled before publication. Its full
6,901,202,944-byte export is preserved; no replacement download was started.

Retained old repair evidence was copied to the volume, verified file by file,
and the original path replaced by a symlink after verification. The fresh source
was copied to local SSD and SHA-256 verified as
`e5991c11352e8abb101d3bd867361be5f508c1a654e64346d6d2e82749551d9f`.
This freed local space for the build without deleting evidence. The cloud storage-placement receipt records the same digest and input size.

Main `b6163d2` puts active builds on local SSD, requires at least 65 GiB free,
preserves the compiler cache, and archives completed generations only after copied
files match their published checksums. Archive corruption, overlapping roots,
incomplete generations and real disk-space guards are covered by filesystem and
SQLite tests. Main `9482a29` adds an explicit optional `reuse_source` parameter;
scheduled runs default to fresh exports.

[Replacement staging run 371550eb](https://prefect-server.waow.tech/runs/flow-run/371550eb-0d44-4502-8ec5-f94fd8ca9b81)
uses that preserved checkpoint on local SSD. The active root is
`/var/lib/typeahead-indexer/active`, archive root
`/var/lib/typeahead-indexer/builds/archive`. The schedule is still inactive and
production publication disarmed. Qualification remains incomplete.

The first SSD retry (`371550eb`) failed during clone cleanup: the earlier root-run
R2 preflight had created a root-owned Python bytecode cache in the worker checkout.
Ownership was corrected, and bytecode writes were disabled for that maintenance
check. No source data was lost or downloaded again.

[Current staging run b908121e](https://prefect-server.waow.tech/runs/flow-run/b908121e-b51d-4fa3-a816-860604523c70)
is the active replacement, using MPS `9482a29` and the same preserved checkpoint.
Use its actual state and logs, not the cancelled/failed earlier runs, for gates.

## Measured disk budget and staging progress

Main `86fce53` raises the next-run preflight to 80 GiB and enforces an 8 GiB
reserve while streaming native output. The current already-running build has a
separate journal-driven guard that stops only its offline builder below that
reserve. The verified source checkpoint's SSD file was atomically replaced by a
symlink to its preserved volume copy after derivation detached it. Only dangling
Buildah images were removed, reclaiming 8.61 GB; all 68 tagged images and live
containerd storage were preserved. The cloud receipts record these changes.

At 03:12:12 UTC, derivation completed with 12,757,757 actors and 517,844,392
prefix associations. The source watermark is 1792894100. SQLite's index sort
then reached its spill/merge phase; the free-space low point was 12.84 GB at
03:22:12, recovering to 30.51 GB as sort spill files were released. This is still
build progress, not qualification or publication success.

Qualification uses the existing production reader with immutable artifacts copied
and SHA-256 checked onto local SSD. This avoids repeating the network volume's
random-read validation bottleneck. It must pass replay, global searches, supported
PDS scopes, excluded-scope failure and unknown-scope empty results. The small R2
manifest promotion is gated on a Completed publisher run, successful qualification,
staging-pointer equality, preserved old-pointer equality and artifact identities.
The production backend image and routing remain unchanged throughout data repair.

At 03:28:55 UTC, group/pack completed all 517,844,392 associations into
147,835,341 keys. The final legacy database is 16,302,268,416 bytes; SQLite
staging was deleted after detachment, restoring 97.37 GB free on local SSD.
The index sort briefly retains approximately 30 GB of spill files alongside its
58.12 GB staging database. Future runs now require 96 GiB free before starting,
so that this measured peak also fits the separate 8 GiB remaining-space reserve.
The original 80 GiB threshold did not budget enough headroom for both.

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

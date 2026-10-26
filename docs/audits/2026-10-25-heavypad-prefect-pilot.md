# Isolated HeavyPad Prefect pilot

The pilot is live behind Tailscale at https://heavypad.tailb660b6.ts.net:10443/.
The canonical hostname still serves the original Hetzner node. That node was not
deleted or resized. This goal moved only the low-risk FastMCP backlog counter.

## Verification

- A checksum-verified k3d 5.9.0 created one k3s v1.35.5 node, matching the current
  cloud node's Kubernetes version. It has its own Docker network and a 6 GiB memory
  ceiling; it did not replace host networking or the existing Docker workloads.
- PostgreSQL and synchronous-AOF Redis have independent persistent volumes.
  Native Prefect API and services use the same immutable image as the original.
- Both application and Kubernetes host ports bind to loopback. The private
  Tailscale HTTPS endpoint was verified from the operator Mac. Existing Serve and
  Funnel entries were preserved. Anonymous and old-credential requests return
  401; pilot-authenticated requests succeed.
- The original root process worker remained active; the pilot uses a distinct
  user worker, endpoint, credential, pool, concurrency one and result directory.
- Real canary `cd329e02-5117-4ea5-9575-61c9a9d2545a` completed with task records,
  logs and an artifact. API/services restart and full isolated-cluster stop/start
  preserved its state and recovered all pods.
- An age-encrypted PostgreSQL dump was copied off host, decrypted privately into
  a separate scratch database and restored. The completed canary row was present;
  the scratch database was then dropped. Daily encrypted backups are enabled.
- Real backlog runs `5a9f6726-a50c-4491-a20c-9ef0f927ef24` and
  `47e960ba-ebf5-428d-bf31-9d1fdff1d078` completed, emitted table artifacts and
  appended the expected counts to the existing small JSONL series. The second
  run used the corrected and verified immutable source pin.

## Single schedule owner

The original deployment `5089a7a2-d3ed-46c6-99bf-314948792a8b` now has no
schedules. Its three queued future runs were cancelled, and no pending/running
backlog jobs remained there. `prefect.yaml` keeps this schedule empty so ordinary
original-instance CI registration cannot reactivate it.

The pilot deployment `75c6a70d-2fcb-46c5-adc9-09522f6f2aea` owns the 30-minute
UTC schedule, `backfill_days=0`, with source
`73681335f5c0a38c24e97a2bf4c3cfbfd90cb7e5`. The first queued recurring runs are
October 26 at 00:00, 00:30 and 01:00 UTC. The 00:00 UTC automatic run `6e55d697-e6f1-4530-9bb1-4686db1e7a6c`
completed at 00:00:02 UTC on October 26, wrote the expected counts and left the
original counterpart cancelled. The canary is manual-only. No schedules
or automations were cloned wholesale from the original database.

## Recovery artifacts and limits

The registry no longer serves the existing UI tag. Its cached 14.4 MiB OCI image
was exported read-only from the cloud node, imported into the pilot, and retained
both on HeavyPad and the operator Mac. Image and archive hashes are in the receipt.
The main server image pulled normally from its immutable registry reference.

The canonical store owns the new pilot auth/database credentials. Consumer checks
are by hash; render is idempotent, a deliberate harmless worker-environment drift
was detected, and canonical bytes were restored. Only the existing GitHub token
block was copied for the selected flow. The separate operator CLI overrides the
API target/auth only in the child process. No existing credential was rotated.

Local backup scheduling and a manual off-host copy are verified. Automatic
backup replication off HeavyPad, catastrophic host-loss recovery and a phone-side
browser login have not been qualified. These remain gates before critical flows
or canonical routing are moved. A single host is an intentional pilot limitation.

Concurrent old/new control planes have independent global concurrency limits.
Do not split writers of the same DuckDB database between them: the existing
`analytics-duckdb-writer` limit would no longer coordinate all writers. The migrated
backlog flow writes a separate small JSONL file and has one schedule owner. Jobs
with shared transactions, variables/cursors, notifications, publishing, deployment
or cleanup effects need a separate migration review.

[Execution receipt](2026-10-25-heavypad-prefect-pilot.json).
[Operations, backup and rollback runbook](../../deploy/heavypad-prefect/README.md).

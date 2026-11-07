# Redis deadline and cancellation rollout

API and services run source `50aec7fb58b6ae0083868c9a7a8f3c02087dca9d`.
Deployment-owner commit `3331ad87ba76f097d47d006a2698ac64ce307092` changes only
their backend image pins. The API-only role, dedicated services role, handler
pool sizes, resource limits, Redis/Postgres state and UI init image are preserved.

## Qualified contract

Redis main `a82e1062e01b594a26cc985ff695d83a6023d2c7` bounds complete write
and response phases through the supplied `Io`, using absolute monotonic
deadlines. Response fragments do not renew the deadline; native bulk header and
body reads share it, including caller delay. Interrupted I/O is canceled and
joined before closing/poisoning the transport. Later commands reject that
client. Finite deadlines require concurrent execution, with an explicit error
when unavailable.

Docket main `8302035016fee3a938ab8a8bfecdb6d65fa0ba2c` and Prefect's publisher
return ambiguous failures without automatically replaying commands. Lost
stream claims remain pending for idle reclamation. Reconnection occurs on the
next explicit operation. Complete Redis error replies preserve the connection.

[Source qualification and reproducer](https://tangled.org/zzstoatzz.io/prefect-server/blob/50aec7fb58b6ae0083868c9a7a8f3c02087dca9d/docs/audits/2026-11-06-shared-io.md).

- Redis: 61 unit/contract/real Redis tests passed on macOS and Linux, with
  Threaded, one/four zio executors and native callers using zio. Covered read
  and write cancellation, deadlines, backpressure, trickled/bulk responses,
  poisoned-client reuse and unavailable concurrency.
- Real Redis negative controls reproduced Docket claiming a second entry and
  Prefect duplicating XADD after suppressing an executed command's reply.
- Prefect: local full backend matrices and 319 unit tests under each application
  backend passed. Docket executes fresh work after blocked-read cancellation.
- Linux amd64 CI pipeline `3mzozlcob3j22` passed both `ci.yml` and
  `publish-image.yml` for the exact source. Included 330 unit tests, four runtime
  compatibility tests, ReleaseFast backend matrices and real end-to-end worker
  execution.
- With one zio executor, the real Redis probe's positive-timeout heartbeat is
  20–21 ms; the old native poll delayed it to 307–410 ms. This establishes
  scheduler responsiveness, not an application throughput improvement.

## Image provenance

Immutable OCI index:
`atcr.io/zzstoatzz.io/prefect-server@sha256:cf429b16de9ccb0c7986ba26d4f1054c688e1cc8ab2e1be5a564a3ff3476a5f9`.
Linux amd64 manifest:
`sha256:066e83a888ee41a3d391f2d82096f9f1bb2c446c87b8949d26cc5270d019c493`.

## Production verification

The existing worker completed each real `prefect-home-canary/canary` run:

| Stage | Run ID | Completed UTC |
|---|---|---|
| Before rollout | `4a23ba7e-ff9c-4b43-8b78-a3c408d3f837` | 2026-11-07 04:14:42.536415 |
| API updated, old services | `c27b10a1-c98b-40a6-a2c9-c89cc1346e25` | 2026-11-07 04:20:46.606829 |
| Both updated | `9f890517-fa4f-48e2-90f0-f7947b274ee0` | 2026-11-07 04:21:54.098309 |

Only API was applied first, then services separately using its Recreate
strategy. API authenticated `/api/version` reports
`0.0.31+50aec7fb58b6ae0083868c9a7a8f3c02087dca9d`. Services `/health`
succeeds; its build metric reports the same SHA, `optimize="fast"` and
`zig_version="0.17.0"`.

Final pods `api-7c7fdbbdf8-ncfd7` and `services-6877f6f45-qc4w8` are ready
with zero restarts and the expected digest. Both targeted Deployment diffs are
empty. The PostgreSQL and Redis pods retain their original names/digests and
zero restarts.

Process snapshots: API 153 threads before / 155 after; services 24 before / 22
after. Kernel I/O worker counts vary. RSS snapshots were API 33,256 / 14,388 KiB
and services 10,164 / 8,408 KiB. After snapshots are fresh pods without matched
warmup/load; they do not establish a memory improvement. Finite Redis deadlines
use a task per phase, which is a fiber under zio and a worker under Threaded.

Rollback: restore backend digest
`sha256:1881ea94e8cd91e4638ea78752fd0a44397454a1336c110fe409bbe9f8ccf6de`
in the owning manifest, land it on main, and apply only the intended Deployment
documents. Preserve roles, pool sizing, resources and the existing UI init pin.

# HeavyPad HTTP pools and process roles

The API keeps 32 native handlers per reactor (128 total). The services metrics
listener uses two handlers instead of 32. The API runs `--webserver-only`; the
dedicated `services` process owns background scheduling, persistence and task
recording. API-local broadcasters and worker dispatch remain enabled.

## Qualification

Server source: `2bc07189b54588161eabae2845886172e2a98346`.
[Qualification and reproduction](https://tangled.org/zzstoatzz.io/prefect-server/blob/2bc07189b54588161eabae2845886172e2a98346/docs/scratch/http-pool-qualification.md).

- Smaller API pools regressed in unpaced, Postgres-backed contention tests;
  retain existing capacity. Counts of 16, 32 and 64 total handlers were tested.
- Real slow-DB requests and four non-reading WebSocket backfill subscribers
  were tested alongside ordinary requests and a healthy subscriber. Bounded
  sends eliminated the old control's 20-second ordinary-request timeouts.
- Two API-only replicas plus dedicated services completed a real deferred task
  submitted through B and executed by a worker subscribed to A, returning `5`.
- Standalone deferred task create/read/filter regression passed against SQLite
  and Postgres after fixing NULL `flow_run_id` decoding.
- Full database and broker matrices, real Redis/TLS/cancellation integration,
  terminal checks and per-commit `zig build test` checks passed locally.
- Linux qualification found and fixed WebSocket cleanup before handler join,
  and HTTP retirement-list cleanup skipped by a shutdown event. Real socket
  negative controls rejected the old orders; the final pins passed 160 native
  server teardowns without leaks or `BADF` warnings.

## Role-only rollout

Deployment owner commit `665605a` added the API command and
`args: [--webserver-only]`, retaining the previous server image. Targeted API and
services diffs were empty before this change. Only the API Deployment was
applied. Canary `5465c3f2-47a6-4e5b-9ed0-5cd9458d4ecb` completed
2026-11-07 02:01:33 UTC. Readback: API 150 threads, services 54; both ready with
zero restarts. Both targeted diffs were empty afterward.

The first candidate image was held after clean Linux CI reported a read-buffer
leak. It was never deployed, and its deployment-main pin was reverted while
qualifying the shutdown fixes.

## Verified image rollout

Deployment owner commit `8789b19` pins API and services to
`atcr.io/zzstoatzz.io/prefect-server@sha256:1881ea94e8cd91e4638ea78752fd0a44397454a1336c110fe409bbe9f8ccf6de`.
Both `ci.yml` and `publish-image.yml` succeeded for source `2bc0718` in pipeline
`3mzotsmlnl522`, including 329 unit tests, all four runtime integration tests,
ReleaseFast backend checks, terminal checks and end-to-end client/worker tests.
The OCI index resolves to Linux amd64 manifest
`sha256:96b64f23eba805295b326ba311d76f9e11a2f42bff9bd01bdea235885b254a8c`.

Only the API Deployment was applied first. Authenticated `/api/version`
reported `0.0.31+2bc07189b54588161eabae2845886172e2a98346`. Canary
`094c995b-d780-47b2-afcf-73b2a53cad3f` completed at
2026-11-07 02:35:23 UTC. Services was then applied separately using its declared
Recreate strategy. Canary `09d2d6fc-5889-4a9e-89a9-ab847542fec8` completed at
02:36:10 UTC.

| Measurement | Original attribution | Role-only rollout | Final readback |
|---|---:|---:|---:|
| API native handlers | 128 | 128 | 128 |
| Services metrics handlers | 32 | 32 | 2 |
| API process threads | 154–158 | 150 | 149 |
| Services process threads | 54 | 54 | 23 |

Process totals include kernel I/O workers, whose count varies. The configured
metrics-handler reduction is 30 threads; the API role change removes duplicate
background consumers. Four API reactors, one metrics reactor, four zio executors
per process, one-CPU quotas and 512 MiB memory limits remain unchanged.

Final pods `api-567476c9-qbdgt` and `services-785666b458-zlb8n` are ready with
zero container restarts and the expected image digest. PostgreSQL
`postgres-84c4954f55-26f4x` and Redis `redis-84789c9ff5-fns2m` were unchanged.
Both targeted Deployment diffs are empty. Services `/health` succeeds and its
metrics report `git_sha="2bc07189b54588161eabae2845886172e2a98346"`,
`optimize="fast"`, `zig_version="0.17.0"`.

Fresh-pod RSS snapshots were 14,256 KiB for API and 8,696 KiB for services.
Comparative memory or throughput measurements require matched warmup and load.
The existing UI init-image pin `3201b828a891b5a78cc6a58ee78a03cf3432fddb` is
preserved.

Rollback: restore the prior immutable server image in the owning manifest,
remove unsupported pool settings if needed, land the change on main, and apply
only the intended Deployment documents. API role rollback is a separate change:
remove its command/arguments to restore standalone background-service ownership.
Do not change the dedicated services role while doing an image rollback.

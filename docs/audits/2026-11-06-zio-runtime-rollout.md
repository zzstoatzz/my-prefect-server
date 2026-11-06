# Zio application runtime rollout

HeavyPad API and services run server main revision
`8c38aeaa032f3c61f558ab274f17972c0eaf76cd` with lalinsky/zio as the application
runtime. Verified November 6, 2026 at 21:26 UTC. The
[receipt](2026-11-06-zio-runtime-rollout.json) records immutable image pins,
deployment generations and completed canaries.

Libraries retain their `std.Io` interfaces. The application supplies four
background zio executors; httpz's main/native threads remain separate. Singleton
synchronization shares the application handle, diagnostic logging has a bootstrap
Threaded handle, and DST still injects its simulated I/O.

The release includes a pinned zio fork fix for native-thread socket close
skipping poll-registration cleanup and hanging an accept after descriptor reuse.
Cleanup forwards to a worker executor without allocating a task. Mixed native
and coroutine socket ownership requires background executors; main-executor-only
zio retains its existing blocking-close behavior. The application always supplies
worker executors. A cold-build option-registration fix also makes image
publication work when zio has not already been fetched.

Pipeline `3mzocgnsrkc22` passed Linux amd64/musl CI and image publication for
this exact server revision. Coverage includes real Redis Docket execution,
blocked-read cancellation, trusted HTTPS and certificate rejection, concurrent
requests, native httpz workers and WebSocket echo, one/four-executor tests,
SQLite/Postgres, memory/Redis brokers, and real Prefect worker execution. Local
macOS and Linux arm64/musl checks passed too. Each implementation commit passed
`zig build test` during a per-commit rebase check.

A 60-second local ReleaseFast comparison at 200 offered requests/s and concurrency
16 measured 180.4 requests/s on zio versus 180.5 on Threaded, zero errors on both,
2.77 versus 2.76 ms median latency, and 123.6 versus 119.4 MB peak RSS. This single
run does not establish production latency or a saturation limit.

The registry-confirmed image index is
`sha256:d4d2d9bfee259344d3c0e37fd0adbf27e81d94343bcbe407606c85477c9c7b9e`;
its Linux amd64 manifest is
`sha256:0e8aa02bd5ea3ebe1bf9ec6dd17e86df44859309f0627846226c2463c71e372d`.
Configuration commit `c5c6b004980c7878abe2eb2948428c06288baaa9` landed on
GitHub main and Tangled main before applying. The baseline manifest diff was
empty; the reviewed rollout diff changed only API/services image pins and
generated deployment generations. API rolled first and returned the expected
authenticated source version before services rolled.

API generation 9 and services generation 8 each have one updated, ready replica,
with zero pod restarts. All background services, including Docket, reported
startup. The final targeted manifest diff is empty. Postgres and Redis retained
their original pods. Resource settings, UI, credentials, schedules and worker
configuration were preserved.

The manual-only `prefect-home-canary/canary` deployment completed before and after
rollout in one attempt each. Post-rollout run
`822b670c-2737-4de7-93bc-22b4ba1776c4` completed at
21:26:14.136291 UTC. The authenticated `/api/version` response was
`0.0.31+8c38aeaa032f3c61f558ab274f17972c0eaf76cd`.

Rollback restores API/services to the prior digest recorded in the receipt via
`stack.yaml` on main, then applies only those two Deployment documents through
`just kubectl`. The clean release checkout preserved unfinished Typeahead edits
in the primary checkout.

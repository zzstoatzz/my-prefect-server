# In-process retry API rollout

Status: API deployed and cloud retry verified, November 5, 2026 at 09:23 UTC.

The deployment already limits concurrency to one. Duplicate infrastructure
executions came from the API offering an existing engine's in-process retry as
fresh worker work. Server revision
`1f6b5f131708855d1730ef20e725292f35536e0c` excludes that marker before the worker
polling limit. No worker, concurrency setting, production timeout or database
schema changes are required. See the
[server incident](https://tangled.org/zzstoatzz.io/prefect-server/tree/main/docs/incidents/2026-11-05-in-process-retry-resubmission.md).

Server CI `3mzkhmlx65q22/ci.yml` passed, including SQLite/PostgreSQL and
memory/Redis checks. Publication `3mzkjadsu4q22/publish-image.yml` passed for the
same immutable source. Earlier publication attempts failed before compilation
at VM bootstrap and during an upstream Lua source fetch; neither was deployed.

The published image index is
`atcr.io/zzstoatzz.io/prefect-server@sha256:3b6be98ec8e48ea462a0fe9d967d2d9a1a56e30229851ca9851529f34d59196d`;
its Linux amd64 manifest is
`sha256:42511b5b91762b479aab52338ec0c458590e9463e30dc08c2bf59197ee162f38`.
Only `Deployment/api` changes. Services retain
`sha256:d5a3f616984023607efc2967b634a1d3465cf7a3afcb9d1e888061d2fb382566`.
The existing one-replica rolling strategy permits one surge and zero unavailable
replicas. The unchanged API manifest produced no live diff before this image edit.

Configuration commit `6814579` was published on both mains before applying only
the API document. Deployment generation 6 reached one ready replica; services
kept their prior image and pod. The [public build readback](2026-11-05-in-process-retry/api-live-build.json)
matches the expected source. All 32 [health samples](2026-11-05-in-process-retry/api-rollout-health.json)
returned HTTP 200 and `true` across the rollout.

The manual cloud probe used immutable source
`d7151f104ff1abff01df58eabe6a3d1af92f3694`, whose main CI passed. Run
`10acbce0-45eb-4d22-96ce-19347f631564` deliberately failed at 09:19:48 and resumed
at 09:21:19. Its [logs](2026-11-05-in-process-retry/retry-probe-log-evidence.json)
show PID 12645 and the same `r0` hostname on both attempts. Its
[state history](2026-11-05-in-process-retry/retry-probe-states.json) ends Completed
with two attempts. Both time-filtered and unfiltered live
[worker polls](2026-11-05-in-process-retry/retry-poll-contract.json) excluded the run
while it was `AwaitingRetry` with the `in_process` marker.

The worker recorded exactly one infrastructure attempt, exit 0 and deletion.
Independent [provider checks](2026-11-05-in-process-retry/retry-probe-cleanup.json)
found no VM, primary IP or SSH key. The temporary unscheduled deployment and its
owned concurrency limit were [removed](2026-11-05-in-process-retry/retry-probe.json).
No corpus credentials or data were used. This verifies retry ownership, not the
pending Typeahead runtime release or a new full snapshot refresh.

The root README was reviewed: setup and runtime roles are unchanged; operational
detail belongs here and beside the stack.

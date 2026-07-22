# Prefect Worker Watchdog Design

## context

HeavyPad runs the production `home-pool` as a Prefect process worker:

```text
systemd -> prefect-worker-guard -> prefect worker start --pool home-pool --type process --name heavypad
```

On 2026-06-22 the Prefect API reported no active `Running`, `Pending`, or `Late`
flow runs, but the HeavyPad worker cgroup still contained roughly 240 stale
`prefect flow-run execute` / `python -m prefect.engine` descendants and was using
about 55 GiB RSS. Manually terminating those child processes dropped host memory
back to normal without affecting any active control-plane run.

That is an unacceptable failure mode for a home worker: the control plane can be
healthy while local worker infrastructure leaks enough process state to OOM the
machine.

## current guard

`tools/prefect-worker-guard` is a small Zig supervisor. It wraps the existing
Prefect process worker; it does not replace Prefect's worker implementation yet.

The guard:

1. Starts the configured worker command as a child process.
2. Places the worker in its own process group.
3. Periodically reads systemd cgroup counters for the owning unit.
4. Checks Prefect's local worker health endpoint when enabled.
5. Emits machine-readable journal logs.
6. Replaces the worker control-process lineage when its local health endpoint or
   worker-channel heartbeat is unhealthy, explicitly excluding descendants that
   carry `PREFECT__FLOW_RUN_ID`.
7. Starts a fresh worker process after a restart.

systemd remains the outer supervisor, cgroup owner, logger, and final OOM safety
rail.

## HeavyPad thresholds

- `MemoryHigh=12G`
- `MemoryMax=18G`
- `MemorySwapMax=2G`
- `TasksMax=800`

These are systemd safety limits, not an estimate that the worker legitimately
needs 12 GiB. The guard observes the counters but does not use aggregate cgroup
pressure to kill the worker or its flows; terminal-descendant cleanup addresses
the known leak directly.

The guard is intentionally outside Prefect. If the worker is the component
leaking processes, relying on another flow to repair it is circular.

On 2026-06-25 the systemd unit and guard stayed alive while the Prefect worker
stopped heartbeating. The cgroup was below the memory/task thresholds, so the
resource-only guard never intervened and `home-pool` stayed `NOT_READY` until a
manual recycle. On 2026-06-29 this repeated in a slightly different shape: the
process worker was still alive, but the server considered `heavypad` offline and
scheduled runs backed up.

The fix is to enable Prefect's documented local worker healthcheck with
`--with-healthcheck` and have the guard check `http://127.0.0.1:8080/health`.
That endpoint tracks successful polling activity and returns 503 when the worker
has stopped polling within Prefect's configured window. This gives the local
supervisor an authoritative worker-liveness signal without polling the Prefect
API.

On 2026-07-01, we found a separate failure mode: local `prefect flow-run execute`
descendants can remain alive after the server has already marked their flow run
terminal. The guard scans the complete systemd unit for descendants with
`PREFECT__FLOW_RUN_ID`, checks that run's state through the Prefect API, and
terminates just those descendant PIDs when the server reports `COMPLETED`,
`FAILED`, `CRASHED`, or `CANCELLED`. Scanning the unit preserves cleanup across
worker generations without granting the guard process-group kill authority.

On 2026-07-21, a malformed server response crashed the worker while multiple
flows were healthy. The guard's unconditional process-group cleanup then sent
`SIGTERM` to every active flow and converted one control-plane failure into
multiple workload failures. The guard no longer terminates a worker process
group for any internal recovery path. Unexpected worker exits leave flow
children running; health-triggered replacement kills the `uv` wrapper and inner
Prefect scheduler but excludes flow-run lineages; terminal cleanup scans the
entire systemd unit so it still finds children from older worker generations.

## future direction

The next version should improve local process awareness:

- track child counts and oldest descendant age directly;
- emit the remaining planned Prefect event `prefect.worker.orphaned-processes-detected`
  (the guard already logs `resource-observed` / `resource-threshold-exceeded` as journal
  events and emits the `prefect.worker.restart-requested` Prefect event);
- eventually subsume enough worker behavior that the guard can become a native
  worker runtime instead of a wrapper around `prefect worker start`.

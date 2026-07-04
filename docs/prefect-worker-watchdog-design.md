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
6. Sends `SIGTERM`, then `SIGKILL` after a grace window, when memory, tasks, or
   local worker health thresholds are exceeded.
7. Starts a fresh worker process after a restart.

systemd remains the outer supervisor, cgroup owner, logger, and final OOM safety
rail.

## HeavyPad thresholds

- `MemoryHigh=12G`
- `MemoryMax=18G`
- `MemorySwapMax=2G`
- `TasksMax=800`
- guard restart at `MEMORY_SOFT_BYTES=12884901888` (12 GiB)
- guard restart at `TASKS_SOFT=500`

These are health-trip thresholds, not an estimate that the worker legitimately
needs 12 GiB. On 2026-06-24, live operation with multiple flow children,
including `rebuild-atlas`, sat around 3-4 GiB. The earlier 900-task shape was
already an incident: a process-worker descendant leak below the previous
24 GiB / 1000-task restart line. The guard should intervene before the worker
gets anywhere near that shape again.

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
terminal. The guard now scans only the worker's own process group for descendants
with `PREFECT__FLOW_RUN_ID`, checks that run's state through the Prefect API, and
terminates just those descendant PIDs when the server reports `COMPLETED`,
`FAILED`, `CRASHED`, or `CANCELLED`. It never uses process-group termination for
this targeted cleanup path.

## future direction

The next version should improve local process awareness:

- track child counts and oldest descendant age directly;
- emit the remaining planned Prefect event `prefect.worker.orphaned-processes-detected`
  (the guard already logs `resource-observed` / `resource-threshold-exceeded` as journal
  events and emits the `prefect.worker.restart-requested` Prefect event);
- eventually subsume enough worker behavior that the guard can become a native
  worker runtime instead of a wrapper around `prefect worker start`.

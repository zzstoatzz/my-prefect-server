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
4. Emits machine-readable journal logs.
5. Sends `SIGTERM`, then `SIGKILL` after a grace window, when memory or task
   thresholds are exceeded.
6. Starts a fresh worker process after a restart.

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
manual recycle. That control-plane liveness signal should be handled by
Prefect's Foreman status transition events and automations, not by adding
Prefect API polling to the local guard.

## future direction

The next version should improve local process awareness:

- track child counts and oldest descendant age directly;
- detect stale or orphaned local descendants without querying Prefect;
- emit Prefect events such as `prefect.worker.resource-observed`,
  `prefect.worker.resource-threshold-exceeded`, and
  `prefect.worker.orphaned-processes-detected`;
- eventually subsume enough worker behavior that the guard can become a native
  worker runtime instead of a wrapper around `prefect worker start`.

# Prefect Worker Watchdog Design

## context

HeavyPad runs the production `home-pool` as a Prefect process worker:

```text
systemd -> prefect-worker-guard -> prefect worker start --pool home-pool --type process --name heavypad
```

On 2026-06-22 the Prefect API reported no active `Running`, `Pending`, or `Late`
flow runs, but the HeavyPad worker cgroup still contained roughly 240
`prefect flow-run execute` / `python -m prefect.engine` descendants and was using
about 55 GiB RSS. Manually terminating those child processes dropped host memory
back to normal without affecting any active control-plane run. Later correlation
showed that the flow clients were stuck shutting down failed WebSocket connections
to the Zig server; this was not an independent process-worker leak.

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
pressure to kill the worker or its flows.

The guard is intentionally outside Prefect because it supervises the worker
control process. It has no authority over the lifecycle of individual flow
processes.

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

On 2026-07-01, the guard added a terminal-descendant reaper based on the belief
that the worker was independently leaking processes. That ownership model was
wrong: a flow reaches terminal state before its client flushes events, closes
WebSockets, and exits. The reaper therefore raced normal teardown and routinely
turned successful infrastructure exits into SIGTERM status 143.

On 2026-07-21, a malformed server response crashed the worker while multiple
flows were healthy. The guard's unconditional process-group cleanup then sent
`SIGTERM` to every active flow and converted one control-plane failure into
multiple workload failures. The guard no longer terminates a worker process
group for any internal recovery path. Unexpected worker exits leave flow
children running; health-triggered replacement kills the `uv` wrapper and inner
Prefect scheduler but excludes flow-run lineages.

## terminal-descendant RCA and removal

On 2026-07-22, the terminal reaper was removed after reproducing both sides of
the failure in isolated HeavyPad environments:

- A stock Prefect 3.7.2 server and process worker returned an ordinary no-op
  flow to the worker's two control processes about 1.5 seconds after the flow
  reached `Completed`.
- A deliberately detached child that inherited the flow engine's output pipes
  reproduced the three-process accumulation. This proves that terminal state
  alone cannot distinguish normal teardown from an escaped workload process.
- Historical production logs tied the long-lived stacks to repeated
  `websockets.client` keepalive and close failures. In one representative run,
  the flow completed, the client remained in failed WebSocket shutdown for 49
  seconds, and the guard then caused the runner's status-143 exit.
- After the Zig HTTP/WebSocket connection-lifetime fix, recent guard matches
  occurred only 0.7–2.6 seconds after completion: the normal teardown window.
- An isolated build of the fixed Zig server completed concurrent Prefect 3.7.2
  runs without WebSocket errors or retained flow processes.

The root fix is the server transport ownership repair. The guard now supervises
only worker liveness and control-process replacement, and it never infers local
process ownership from a control-plane terminal state.

## future direction

If worker liveness remains stable, the next simplification is to remove the Zig
wrapper entirely and let systemd supervise `prefect worker start` directly. That
requires a separate soak of the documented local healthcheck failure modes; it
is not coupled to flow-process cleanup.

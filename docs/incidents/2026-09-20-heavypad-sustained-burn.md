# heavypad sustained CPU burn — 2026-09-20

The hourly diagnostics alert identified a Go executable named `simulator`, PID
3999530. This was ongoing CPU use, not merely an elevated lifetime average:
between 21:37:10 and 22:37:14 UTC, its accumulated CPU time increased from
55,326 to 58,895 seconds (3,569 CPU seconds over 3,604 elapsed seconds, about
99% of one core). At the latter sample it had lived for 54,923 seconds,
placing its start around 07:21:51 UTC (02:21 Chicago).

Executable reported in the event payload:

```
/home/stoat/.cache/go-build/40/4059dcf4520d26db79d6c8d39de2392500654a7771ccb7aaedbaad9b7b15b213-d/simulator
```

The host was not saturated: the 22:37 diagnostics run reported load 1.15 over
32 CPUs, 56.38 GiB available out of 62.63 GiB RAM, and 800.36 GiB disk free.
Diagnostics completed in the intentional `Degraded` completed state; its worker
process exited cleanly.

Evidence: production flow run
[diagnostics-88bce46b](https://prefect-server.waow.tech/runs/flow-run/88bce46b-1c7d-4ee7-a2de-2f7dba054a7d),
the preceding five hourly diagnostics state messages, and
`diagnostics.sustained-burn` events at 20:37, 21:37, and 22:37 UTC.
Read through the repository's authenticated `just prefect api` recipe.

`flows/diagnostics.py` flags processes at least two hours old, with at least
one accumulated CPU-hour and lifetime CPU/elapsed ratio >= 0.5. The automation
in `deploy/automations.yaml` repeats hourly deliberately. Its single-sample
calculation does not itself establish current activity; the successive event
samples above do.

After Tailscale identity verification, SSH confirmed the executable above and
working directory `/home/stoat/jetstream`. Its parent was `go`, PID 3999471,
itself reparented to PID 1. The intended lifetime has not been established.

At the user's explicit request, sent SIGTERM to PID 3999530 after verifying its
executable path. It remained present at the two-second check, then exited before
the follow-up check; no SIGKILL was needed. Both simulator and its Go parent
were absent afterward, and `pgrep -x simulator` returned no matches. The Prefect
worker remained active and its local health endpoint returned `{"message":"OK"}`.
No production configuration was changed.

## recurrence at 23:49 Chicago

The original process stayed stopped. A new simulator, PID 518555, started at
20:04:39 Chicago (2026-09-21 01:04:39 UTC), with parent `go` PID 518496.
Both were in `prefect-home-worker.service`'s cgroup, working in
`/home/stoat/jetstream`. Its arguments were
`serve --reset --accounts=100 --commits-per-sec=20`.

The launcher is established by production run
[stream-admission-76f51e20](https://prefect-server.waow.tech/runs/flow-run/76f51e20-5ebc-4d78-82f9-7fb8b2c8fd88):
it logged "starting the pinned simulator on :7777" at 01:04:40 UTC and
completed its gate successfully at 01:22:59 UTC. `ensure_simulator()` explicitly
leaves this process running between admission runs for reuse. Thus the burn
is real, but its continued lifetime is intentional in the current implementation;
the diagnostics expectation and simulator lifecycle disagree.

Successive diagnostics events recorded 6,686 CPU seconds at 03:37:17 UTC
and 9,876 at 04:37:14 UTC: about 89% of one core during that hour. Live host
load was 1.19 over 32 CPUs, CPU pressure averages were zero, and the worker
was active with a healthy endpoint. No further process was stopped in this
read-only follow-up.

## resolution

`stream-admission` now owns the simulator process group for the duration of a
gate and stops it in a `finally` block on both success and failure. A simulator
orphaned by an older run is adopted and stopped after the next gate. The launch
no longer uses `nohup` or a background shell, so the flow retains an exact
process-group identity; teardown sends SIGTERM and escalates to SIGKILL after a
bounded grace period. A regression test launches a real leader plus child and
proves teardown removes the whole group.

At 00:02 Chicago on 2026-09-21, the lingering group from the completed run was
identified by PID, PGID, executable, working directory, listener, and worker
cgroup, then stopped with SIGTERM. Both the `go run` parent and simulator child
exited, and port 7777 was no longer listening; escalation was not required.

Production smoke run
[`stream-admission-a70d63be`](https://prefect-server.waow.tech/runs/flow-run/a70d63be-af7a-488e-b2b4-f47ce9472c28)
then exercised the deployed fix from commit `2759d18`: the Stream unit gate
passed, `stop_simulator` completed for process group 738793, and the flow exited
cleanly. A separate SSH check afterward found no simulator process and no
listener on port 7777.

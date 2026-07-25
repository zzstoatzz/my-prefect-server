---
name: home-pool-worker
description: Diagnose the home-pool process worker on heavypad, where all flow runs actually execute. Use when a flow run is stuck in RUNNING, a run died without logs, the worker seems down, or you need to reach the worker box or restart it safely.
---

# home-pool worker (heavypad)

All flow execution happens on a **process** worker on the home box (heavypad), a
systemd unit polling the server outbound over Tailscale. Config lives in
`deploy/home-worker/`. The server itself runs elsewhere (see
[[server-access]]) — a healthy server tells you nothing about whether flows can
run.

## reaching the box

```bash
tailscale ssh stoat@heavypad
```

There is a check-mode auth gate. If the hostname fails to resolve
(`nodename nor servname provided`), local Tailscale is down — that is a local
problem, not evidence the worker is down. Say so rather than reporting drift you
could not observe.

## the unit and its guard

`prefect-home-worker.service` does not start the worker directly. Its
`ExecStart` runs `prefect-worker-guard --config /etc/prefect-worker-guard.env`,
which starts the pinned worker command and restarts it on healthcheck failure.

The critical property: **the guard may replace an unhealthy worker process, but
it never signals active flow-run processes.** Flow runs survive worker
replacement. Preserve that invariant in any change to the guard — killing the
process group would take live runs with it.

Relevant history, so you don't re-litigate settled decisions:

- `43c9c76` replace worker lineage without killing flow runs
- `9f765fe` preserve active flows across worker restarts
- `30ce4d7` retire leftover scheduler after wrapper exit
- `962d1fb` remove terminal flow process reaper (removed deliberately — do not
  reintroduce a reaper)

Worker webserver binds `127.0.0.1:8080`. Memory is bounded by
`MemoryHigh=12G` / `MemoryMax=18G` with `TasksMax=800`.

## stuck in RUNNING

A worker restart can orphan in-flight runs: the flow-run process is gone but the
server still believes the run is `RUNNING`, and nothing will ever transition it.
The guard restarting on healthcheck failure is a common cause.

These do not self-heal — the run sits until someone moves it. Confirm the process
is actually absent on heavypad before forcing state, then set a terminal state
via the API. Don't assume a long-`RUNNING` run is orphaned; a genuinely slow flow
looks identical from the server side.

## checking on it

```bash
tailscale ssh stoat@heavypad 'systemctl status prefect-home-worker --no-pager'
tailscale ssh stoat@heavypad 'journalctl -u prefect-home-worker -n 100 --no-pager'
just prefect work-pool ls
```

Analytics live at `/home/stoat/prefect-analytics` as hostPaths — DuckDB,
spend log, and local storage. Flow runs execute in an ephemeral `/tmp` dir, so
those paths only exist in a run because `prefect.yaml` injects them
([[deployments]]).

`uv` must be on `PATH` for the unit (`/home/stoat/.local/bin`), since the process
worker spawns `uv run ...` per flow run.

## after changing the unit

`deploy/home-worker/install.sh` installs it. If someone ran that from a worktree,
the box can be ahead of or behind `main` — the box is not covered by the
git-clone-at-runtime guarantee that keeps flow code in sync. Verify the installed
unit against `main` rather than assuming.

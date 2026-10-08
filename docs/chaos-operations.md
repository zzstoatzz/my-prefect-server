# chaos operations

This repository owns the Prefect instance: placement, persistent storage,
workers, deployment configuration, and operational procedures. The sibling
[prefect-server](https://tangled.org/zzstoatzz.io/prefect-server) repository owns
the server implementation, reusable fault harness, regression tests, and their
acceptance evidence. Keep the harness beside the code it tests; use this guide
when operating the deployed instance.

## topology

The control plane runs on the existing Hetzner k3s node. Helm manages two API
replicas and one background-services replica. PostgreSQL and durable Redis
remain in that cluster. Two API pods protect against individual process/pod
loss, not failure of the shared node.

HeavyPad runs the home-pool process worker, the private self-hosted MCP, and an
isolated chaos lab. Routine faults target disposable lab containers, never the
production database, Redis, or application workers. The lab uses an immutable
tested server image and separate data volumes.

## inspect

From the instance checkout, use `just status` and `just prefect flow-run ls`.
The server checkout provides `just prod-status`, `just prod-drift`, and
`just prod-verify`; the latter runs diagnostics through the real home worker.
Always use the explicit production kubeconfig rather than an ambient context.

On HeavyPad, as the worker owner:

```sh
systemctl --user list-timers prefect-chaos.timer
systemctl --user status prefect-chaos.service
journalctl --user -u prefect-chaos.service -n 30
systemctl --user status prefect-mcp-selfhosted.service
```

The MCP's `get_identity` confirms its Prefect target. `get_server_status`
reports readiness/version observations; it does not prove work completes or
identify every replica. `get_chaos_status` reports lab records and admission
limits; it does not inspect live systemd state.

## failures and limits

Reports and accounting are under `~/.local/state/prefect-chaos-validation`.
A failure writes `STOPPED.json`; inspect the report and logs, confirm cleanup,
and correct the cause before archiving the reviewed latch. Keep the original
report and accounting. An `active.json` record requires checking the actual
process before deciding it is abandoned.

The runner and containers have independent CPU, memory, task, and duration
limits. The timer randomizes its delay and the runner selects a fault using a
recorded seed. Budget skips are normal admission decisions, not failed tests.
See the server repository's `scripts/chaos/` and `docs/chaos/` for the current
limits, unit templates, reproducible commands, and retained evidence.

## production changes

Use Helm through the server repository's deployment recipes. Do not use
`kubectl set image`, `edit`, or `scale` to bypass Helm ownership. A deliberate
single-pod smoke test is available as `just prod-replica-smoke <record-directory>`;
run it only as an explicitly authorized production test with event and workflow
checks. Routine timers must never invoke it.

On October 8, 2026, the production smoke test removed one API pod, retained the
survivor, restored two ready replicas in four seconds, and preserved all 64
canary events. Real diagnostics completed before and after. Reports are in the
server repository's `docs/chaos/2026-10-08/`.

The Redis migration and manifests are on `codex/prefect-chaos-durability` in
this repository. Production already uses the durable primary and retains the
old Redis as its replica. Reconcile that branch before applying infrastructure
from another checkout: an older manifest may route traffic back to the old
instance. This guide does not make the current branch deployment-ready.

For draining home-pool, use queue pause and verify no new pickups: the deployed
server has a known omission in honoring the pool pause flag. Resume the queue
after maintenance and verify actual workflow throughput.

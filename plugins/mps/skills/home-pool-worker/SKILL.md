---
name: home-pool-worker
description: Inspect HeavyPad process workers, diagnose stalled runs and verify worker configuration against the production deployment's pool.
---

# home workers

Read the [home worker runbook](../../../../deploy/heavypad-prefect/README.md#workers-and-flow-interpreters).
The control plane and process workers run on HeavyPad. Select the worker from
the deployment's pool; there is no single `home-pool` worker covering everything.

```sh
just heavypad-status
just prefect work-pool ls
ssh stoat@heavypad 'systemctl --user list-units "prefect-heavypad-worker*" --all --no-pager'
```

Check `worker.service`, `worker@.service` and their instance overrides in
`deploy/heavypad-prefect/` against the installed user units. They own capacity,
interpreter and restart behavior. Drain affected jobs before stopping a worker:
current units use `KillMode=control-group`. Do not apply the retired system
worker's process-survival assumptions to these units.

A long-running server record is not proof of an orphan. Check the actual host
process and logs before forcing a terminal state. A failed SSH connection is
not evidence that the worker is down; distinguish local tailnet access from
observed host state. Check your own workload's resource use first.

Persistent analytics paths come from deployment configuration, not the process
worker's temporary working directory. Preserve the shared DuckDB writer lease.
The separate exe worker was disabled at the November 15 audit; enabling it or
resuming autofix is a separate operational decision.

---
name: adhoc-runs
description: Run a flow locally for debugging or through its production deployment, accounting for credentials, persistent data and external effects.
---

# ad hoc flow runs

Inspect the flow's entrypoint, parameters and effects before running it. Local
execution uses local source and may use different storage and credentials from
the production worker; it does not exercise pull steps or job-variable injection.
Use `uv run` for Python and the existing tests for pure logic where possible.

A deployment run exercises its configured worker and immutable source/artifact:

```sh
just prefect deployment run 'diagnostics/diagnostics' --watch
```

It does not run uncommitted local edits. Git, wheel and image delivery differ;
read [deployment validation](../../../../docs/deployments-validation.md).
Runtime secrets come from Prefect blocks; operator access is described in
[operations](../../../../docs/operations.md).

Read the actual dry-run contract where available. A rerun may publish PDS records,
R2 objects, Pages or notifications; idempotency of one publisher is not a general
property of flows. Do not trigger external effects as part of a read-only audit.
For production verification, inspect the terminal state name and output as well
as state type: `Completed` alone does not establish complete upstream coverage.

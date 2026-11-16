---
name: server-access
description: Inspect the production Prefect server or home cluster, resolve operator access, and choose the correct target.
---

# server access

Read [operations](../../../../docs/operations.md) for the access contract.
Use `just prefect` and `just kubectl`: they resolve the production target from
`deploy/targets.toml`. Prefect credentials come from the canonical SOPS store;
cluster access uses SSH into the home k3d node, namespace `prefect-home`.
An empty shell environment is not evidence that credentials are missing.
Never print secrets, switch global profiles or fall back to ambient Kubernetes.

Prefer the connected read-only Prefect MCP for queries and confirm its endpoint
with `get_identity`. The Cloud connector is a different service. Inspect
`plugins/mps/.mcp.json` before diagnosing plugin configuration.

```sh
just prefect deployment ls
just prefect api POST /flow_runs/filter --data '{"limit": 5}'
just kubectl -n prefect-home get pods
just heavypad-status
```

Use JSON for full IDs and inspect returned records before trusting filters.
For SDK access, `just prefect --script /path/to/script.py` supplies the same
explicit target and authentication. The wrappers do not grant permission to
mutate state merely because an investigation needs credentials.

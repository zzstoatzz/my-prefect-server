# operations

Production runs on HeavyPad. The old Prefect Hetzner server (142141293), its
150 GB volume and two primary IPs were deleted on October 27, 2026, after
migration; see the [retirement receipt](audits/2026-10-27-prefect-retirement.md).
The removed setup commands and deployment definitions belonged to that node.

## access

Use the repository wrappers, which select the production endpoint and host from
[targets.toml](../deploy/targets.toml) without changing global profiles or the
laptop's Kubernetes context:

```sh
just prefect deployment ls
just prefect flow-run ls
just kubectl -n prefect-home get pods
just heavypad-status
```

The Prefect wrapper reads `heavypad_prefect.api_auth_string` from the canonical
SOPS store using the operator's existing age identity. Kubernetes access uses
SSH and kubectl inside the home k3d node; it does not use a root `kubeconfig.yaml`.
Do not print credentials to diagnose access. `just` also loads the gitignored
`.env` for tools with their own operator environment, including the MCP plugin.
Runtime credentials are Prefect Secret blocks, resolved at run startup by
`mps.secrets_plugin` or loaded explicitly through `mps.blocks`.

For SDK operations that need the same authenticated target, write a script and
run `just prefect --script /path/to/script.py`. Prefer the connected read-only
Prefect MCP for inspection; confirm its endpoint with `get_identity`.

## source and deployment

[The home manifest](../prefect.heavypad.yaml) owns this repository's production
deployments. [Standalone automations](../deploy/automations.yaml) are applied
separately with `just automations`; deployment registration does not reconcile
those notification and cross-deployment rules.

Publish source to main with `just push` after the required checks pass. CI
validates source; the trusted home registration timer waits for green CI for
that exact revision, validates its release pin, and registers it. Follow the
[home runbook](../deploy/heavypad-prefect/README.md) for registration receipts,
worker operations, server rollout and backup/recovery. A successful registration
is not proof of successful execution: verify the intended worker, real run and
output before calling a change shipped.

Git-backed deployments pull their declared revision. Wheel/image-backed jobs
keep their own immutable artifacts. A source push does not update an older
wheel. [Deployment validation](deployments-validation.md) explains these contracts.

Prefect coordinates ingestion and downstream workflows. Dagster owns dbt asset
execution and metadata; see [analytics](analytics.md) and the
[Dagster runbook](../deploy/dagster/README.md). Hub, push, proxy and monitoring
services use the committed definitions in [prefect-edge](../deploy/prefect-edge/),
installed by `just edge-install REVISION` after publishing that revision.

## recovery and history

Use the [outage runbook](heavypad-outage-runbook.md) for host recovery and manual
Cloud recovery paths. Never enable a second scheduler for the same effects.
Dated [migration records](migrations/) and [audits](audits/) preserve past
qualification; they are not installation procedures for the current system.

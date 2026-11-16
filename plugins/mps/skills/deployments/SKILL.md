---
name: deployments
description: Change or debug home Prefect deployments, schedules, triggers, code delivery, runtime secrets and worker dependency failures.
---

# deployments

`prefect.heavypad.yaml` owns this repository's home deployments.
Read [deployment validation](../../../../docs/deployments-validation.md) and the
[registration runbook](../../../../deploy/heavypad-prefect/README.md#registering-main-revisions)
before changing delivery or registration.

Run `just validate-deployments` and the checks appropriate to the change.
Publish main before rollout. CI checks source; the trusted home service registers
only an exact green main revision. Verify its receipt, the intended worker,
a real deployment run and output. Registration success alone is insufficient.

Preserve the declared delivery mode. Git-backed jobs pull their declared pin;
wheel-backed jobs with `pull: []` use dotted module entrypoints unless the worker
supplies files. Image jobs use immutable images. A source push does not replace
an older wheel or image. Package and checkout pins must agree.

Runtime credentials are Secret blocks, resolved at startup by
`mps.secrets_plugin` or explicitly through `mps.blocks`. Do not bake credentials
into deployment registration. Keep interpreter/dependency overrides in
`work_pool.job_variables.command` and preserve command-prefix Prefect settings.

Read the selected pool and persistent paths from the manifest. Never enable a
second scheduler for the same effects or silently resume a paused deployment.
Standalone automations in `deploy/automations.yaml` are reconciled separately.
Dagster owns dbt asset execution; Prefect's transform flow submits and awaits it.

# deployment validation

`scripts/validate_deployments.py` owns the offline checks for this repository's
code-delivery contracts. It reports errors; it never changes deployment config,
imports flows, executes pull steps, or contacts Prefect.

```sh
just hooks                         # install pre-commit for this checkout
just validate-deployments           # quick static check
just check                         # static check plus the existing full suite
MPS_PIN="@$(git rev-parse HEAD)" just validate-deployments --release
```

The commit hook, `just validate-deployments` and CI all check
`prefect.heavypad.yaml`. Install the hook once per clone with `just hooks`;
do not bypass it. CI validates source without production credentials. The
trusted-host registration service additionally runs `--release` using the same
full `MPS_PIN` that it registers. See the
[registration runbook](../deploy/heavypad-prefect/README.md#registering-main-revisions).

## what it checks

- YAML anchors are resolved. Deployment-level `pull: []` replaces the global
  pull steps. A custom work-pool mapping does not inherit the environment of
  the similarly named anchor.
- A deployment that installs the mps wheel, with no pull steps or explicit
  image/worker path supplying files, must use an importable
  dotted entrypoint. The flow module must be declared in the mps wheel's Hatch
  force-includes, and the command or worker `local_packages` must include mps.
- Git package requirements use either the shared MPS_PIN template or a full
  explicit commit SHA. When pulling source, job env.MPS_PIN must match that
  requirement. Release mode requires a full SHA for the shared template.
- File entrypoints for the recognized Git route exist in this checkout. Basic
  config shape, entrypoint syntax, and duplicate deployment names are checked.
- A file entrypoint loaded from the source checkout whose command does not
  install mps must not import mps while loading, directly or through other
  `flows/` modules. The check parses imports with `ast` and follows only
  module-level imports, so imports inside functions and `TYPE_CHECKING` blocks
  are allowed. Other third-party packages are not checked.

This catches the 2026-11-07 mcp-fleet-health crash, where a shared helper
module gained an mps import and the light deployment failed while loading.
It also catches the September 11 reconciliation regression: converting a wheel's
`flows.watch_tangled_pulls.watch_tangled_pulls` into
`flows/watch_tangled_pulls.py:watch_tangled_pulls` while keeping `pull: []`.
The inventory renderer accepts both forms; documentation formatting must never
choose the runtime's loading mode.

## what remains unverified

A passing check means the static contracts passed, not that a deployment will
run. Output explicitly lists unverified routes. An image-backed file entrypoint
with no pull steps can be valid; it is not rejected just because it uses `:`.
Unknown delivery mechanisms are reported for operator review, not certified.

Remote wheel contents and availability, old pinned Git revisions, package
installation, worker environment, arbitrary shell steps, network access,
credentials, import side effects and flow behavior require other checks. The
current checkout's wheel manifest does not prove an older wheel contains a
module. No Prefect schema compatibility or flow signature validation is claimed.

The next layers should inspect the exact deployed artifact, then load the flow
inside the intended worker environment using Prefect's loader with placeholder
fallback disabled. Keep the source checkout off the import path; never invoke a
flow body merely to test its entrypoint. These layers are not implemented yet.

## registration and runtime verification

Spindle runs the source checks in a NixOS microVM. It does not register
production deployments. HeavyPad waits for green CI for the exact main revision,
validates the target and release pin, and registers the home manifest.

Standalone automation writes remain a separate operation. This validator does
not check notification routing, automation diffs or live pause state. Preserve
intentionally paused deployments and verify real worker execution after an
authorized rollout. Successful source checks and registration do not certify
artifact availability or successful flow execution.

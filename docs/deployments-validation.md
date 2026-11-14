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

The [pre-commit hook](https://pre-commit.com/#usage) checks staged content on
commit. CI invokes the same validator before the test suite and again, with
`--release`, immediately before registering deployments. Hooks can be skipped;
CI is the delivery gate. Direct `just prefect deploy` and API mutations bypass
that gate: run the release check first, using the same MPS_PIN for registration.
Installing the hook is required in each new clone; `just hooks` does that without
adding the entire application's dependencies to the hook environment.

## what it checks

- YAML anchors are resolved. Deployment-level `pull: []` replaces the global
  pull steps. A custom work-pool mapping does not inherit the environment of
  the similarly named anchor.
- A deployment that installs the mps wheel, with no pull steps or explicit
  image/worker path supplying files, must use an importable
  dotted entrypoint. The flow module must be declared in the mps wheel's Hatch
  force-includes, and the command or Sprite `local_packages` must include mps.
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

## breadth review and follow-ups

The September 11 review covered flows and mps helpers, package build manifests,
the Sprite worker and home-worker units, automation registration, CI/just
recipes, inventory rendering, tests, and operator documentation.

| Boundary | Finding and disposition |
|---|---|
| Configuration to runtime | Five wheel routes retain dotted entrypoints. Regression checks now live in the validator tests rather than the documentation renderer tests. |
| Package to checkout | 24 custom job mappings lacked MPS_PIN propagation. Their source checkout now receives the same declared pin as the installed package. Existing explicit legacy pins remain unchanged. |
| Package ownership | The root distribution supplies mps helpers; the separate mps wheel additionally force-includes selected flow modules. A checkout import is insufficient evidence of wheel contents. |
| Execution | Home process jobs and Sprite jobs coexist. README and operations now describe both and point here for the delivery contracts. |
| Registration | CI still registers all deployments on a main push. Selecting changed deployments, comparing live state, preserving a rollback snapshot, and verifying completion need a separate rollout change. |
| Separate presence config | `deploy/presence.yaml` has its own source variables and `just presence-deploy` guard. This first validator gates the main manifest; it does not yet establish presence revision binding. |
| Automation writes | `scripts/apply_automations.py` is a separate mutation path. Static deployment validation does not check notification routing, recursive autofix triggers, or automation diffs. |
| Historical notes | Local operator notes outside the checkout and archived build logs describe past snapshots. Use current configuration and live state for operational decisions. |
| Runtime tests | Unit tests and successful registration do not certify artifact imports or successful worker execution. Artifact and worker preflight remain the highest-priority follow-ups. |

General entrypoint/storage preflight may belong in Prefect. This repository owns
its mps wheel layout, Heavypad paths, Sprite package declarations and revision
binding. Keep these policies here; an upstream validator should allow legitimate
image and worker-path deployments.

## CI execution environment

The deployment workflow uses Spindle's NixOS microVM engine with explicit Nix
packages. The check and registration steps retain the same validation gates.
On October 17, Docker on HeavyPad rejected the Nixery registry manifest because
its HEAD response omitted Content-Length. A direct pull reproduced the failure;
GET returned a valid manifest. The microVM engine provisions dependencies through
Nix and avoids this failing OCI registry path. Verification requires a completed
CI run through registration, not merely successful VM creation.

## legacy Kubernetes workers

Retained Kubernetes deployments require the `-kubernetes` Prefect image variant,
namespace `prefect` in the pool template, and
`PREFECT_INTEGRATIONS_KUBERNETES_OBSERVER_NAMESPACES=prefect` for scoped RBAC.
Interpreter and dependency overrides belong in `work_pool.job_variables.command`.
These requirements do not select or reactivate a deployment.

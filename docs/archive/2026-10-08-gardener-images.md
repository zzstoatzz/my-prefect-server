# Gardener image rollout, 2026-10-08

The three `gardener-exe` deployments now use digest-pinned images. The worker
remains on heavypad, with its existing inference-grant ownership. Every attempt
still gets a fresh VM. Runtime dependency installation is disabled by explicit
empty `requirements` and `local_packages` in each deployment.

## Code preservation

[The release manifest](../../deploy/gardener/images.json) maps deployment IDs,
image digests, and original wheel SHA-256 hashes. We copied the live deployment
wheels from heavypad, verified their hashes, and installed each into its own
locked image. The build compares every shipped Python module with the installed
file. Verification covered 48 modules for investigation, 41 for autofix revision,
and 39 for patch testing. Each actual Prefect entrypoint also loaded successfully
from its image. The application wheel was not replaced by current checkout code.

The image layer pins Prefect to 3.7.7, matching the bootstrap job configuration.
The separate long-running worker still uses its existing environment.

## Private registry

`gardener-registry.exe.xyz` stores images on a bind-mounted persistent directory.
The container restarts automatically, and Docker is enabled at VM boot. The
proxy is private with no shares; an unauthenticated `/v2/` request redirects to
login. Boot pulls use exe.dev's documented same-account private-registry path,
authenticated by the existing worker SSH identity. No new registry credential
is passed to jobs or stored in Prefect.

A registry container restart preserved all four release tags. Worker-side
image creation from the immutable investigation digest succeeded. The registry
is intentionally persistent and outside the worker's job-name prefix.
Operational details and reconstruction inputs are in the
[registry guide](../../deploy/gardener/REGISTRY.md).

## Live evidence

- The worker-side image probe passed exit, detached-child cleanup, preservation
  of outcome after supervisor restart, cancellation, and verified deletion of
  both test VMs. It used the production worker's SSH identity from heavypad.
- Read-only investigation `7ac8172c-b6a8-4dd1-8cbc-e53b51c96de0` completed on the
  pinned image with exit code 0. Its saved `pi-agent-output` artifact is
  `7c7b9506-6e3b-4d7a-a341-9cb5e34003c4`.
- The observation records the expected image digest and final phase `deleted`.
  VM creation took 3.07 s, environment preparation 2.68 s, supervisor start
  0.79 s, and deletion 2.92 s. These are one run's measurements, not percentiles.
- Production investigation was the first deployment switched. The remaining
  two were switched only after that canary completed and its VM was deleted.
- After the registry restart, fresh VMs pulled the autofix-revise and
  test-pull-patch digests, loaded their actual Prefect entrypoints, checked the
  prepared agent runtime and systemd access, and were deleted. No PR-writing
  or revision flow was triggered just to test infrastructure.
- All three live deployment configurations were read back: image mode, exact
  release digest, empty requirements/local packages, unchanged entrypoint,
  `gardener-exe` pool, and 2400-second timeout. The worker remained active on
  release `629e9ba`, with zero restarts.

## Discovered server limitation

The first canary, `ffc5c262-0e3a-4f0c-9446-b05bf39769d9`, used per-run image
overrides. The API saved those overrides, but the scheduling response omitted
them, so the worker ran bootstrap mode. We verified the VM's configuration and
did not count that run as an image test. It completed before cancellation was
requested, and its VM was deleted normally.

The successful canary used deployment-level job variables. The scheduling
serializer is `prefect-server/src/api/work_pool_schedule.zig:writeFlowRunObject`;
its per-run overrides need a separate server fix. Do not use per-run image
overrides as rollout evidence until that is corrected.

## Rollback and limits

Private pre-change API snapshots and locked build contexts are saved on heavypad.
Rollback restores the previous `job_variables` and the corresponding wheel
configuration in `prefect.yaml`; see the registry guide for the command shape.
No worker restart or placement change was needed.

The registry has persistent storage and a recoverable build path, not high
availability. The account still reports an October 11, 2026 trial expiry.

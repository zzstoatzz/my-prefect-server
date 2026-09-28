# Cache pinned source for home-pool pull steps

Status: implemented and deployed to the existing diagnostics deployment, 2026-09-28.
The ad-hoc verification succeeded. See `docs/audits/2026-09-28-diagnostics-cached-pull.md`
on branch `codex/cached-diagnostics-pull` for release and validation details.
The design below records the rollout plan; no new deployment or worker change was needed.

## Decision

Use a custom pull step in the existing `mps` package. No worker modification is
needed. The current job command installs a pinned `my-prefect-server` package
before `prefect flow-run execute`, so the pull-step implementation can already be
imported when Prefect prepares the flow's source.

The repeated network transfer comes from the separate top-level shell pull step:
it deletes a run-local checkout and clones the repository again. A warm `uv` package
cache does not eliminate this second clone.

Proposed module: `packages/mps/src/mps/deployment_steps.py`.
Proposed function: `cached_checkout`.

```
existing pinned uv environment
  -> prefect flow-run execute
  -> mps.deployment_steps.cached_checkout
       -> read and validate MPS_PIN
       -> reuse cached source for that exact commit, or fetch once
       -> materialize independent source files in this run's workspace
       -> return directory + commit + cache_hit
  -> Prefect imports and runs the flow from that directory
```

## Contract

- Cache key includes the repository identity and a full immutable commit SHA.
  Read the existing `MPS_PIN` environment variable, stripping its leading `@`.
  Reject empty pins, branch names, and malformed SHAs. Never fall back to main.
- Cache belongs to the existing worker user at
  `/home/stoat/.cache/mps/source/`. It can be created by the pull step itself;
  no service changes, daemon, root permissions, or new credentials are required.
- For the initial implementation, cache a source archive per commit. On a miss,
  fetch the requested commit into temporary Git storage, verify the resolved
  commit, create the archive, and publish it with an atomic rename. Prefer a
  shallow fetch of the exact SHA; validate support against the real canonical
  remote during the diagnostics trial. Any mirror fallback must fetch and verify the same SHA.
- A hit performs no remote fetch or freshness check: a commit is immutable.
- Use a per-repository filesystem lock for cache publication, materialization,
  and pruning. Parallel misses for the same commit must download once.
  Include bounded Git subprocess timeouts and clear failure logs.
- Extract into the current run's temporary workspace, not a shared checkout.
  Publish that destination only after complete extraction. Do not use writable
  hardlinks, a shared working tree, or symlinks back into the cache.
- Return `{"directory": <absolute run path>, "commit": <sha>, "cache_hit": <bool>}`.
  Prefect's flow loader consumes `directory` before importing the flow. A second
  built-in `set_working_directory` step is unnecessary for this contract.
- Bound cached archives by last access: initially keep at most eight commits and
  a 512 MiB total budget, pruning under the same lock after successful extraction.
  Expired commits can be fetched again for rollback runs. An oversized single
  artifact can serve the requesting run and be evicted after extraction.
- Once extraction completes, the run no longer depends on the cached archive.
  This makes pruning safe without a custom worker lifecycle hook or leases
  lasting for the entire flow run. Temporary fetches must be cleaned on ordinary
  failures; abandoned staging directories need age-bounded cleanup under the lock.

This removes repeated WAN downloads and Git history checkout work. It still
does local extraction/copy I/O to preserve run isolation. Removing that I/O too
would require stronger assumptions about flows never writing into their source
tree; that is unnecessary for the bandwidth goal.

Source archives do not contain `.git`. Before rolling out to an existing flow,
check whether it needs this repository's Git metadata or Git LFS/submodules.
No tracked submodules or LFS attributes were found in the current repository.
Flows cloning their own separate repositories are unaffected by this cache.
The custom step must be installed in the pinned package, not only placed in the
repository that it is itself responsible for retrieving.

## First rollout: existing diagnostics deployment

Use the existing `diagnostics` deployment (`flows/diagnostics.py:diagnostics`) on
`home-pool`. It already reports host telemetry and proves the worker can retrieve
and execute code. Add a deployment-specific `pull` override to this entry in
`prefect.yaml`; preserve its hourly `37 * * * *` schedule, parameters, pool, and
telemetry behavior. No new flow, deployment, or feature flag is needed. The shared
top-level pull remains unchanged for other deployments.

Illustrative configuration, not yet installed:

```yaml
deployments:
  - name: diagnostics
    tags: [watch]
    entrypoint: flows/diagnostics.py:diagnostics
    pull:
      - mps.deployment_steps.cached_checkout:
          repository: https://tangled.org/zzstoatzz.io/my-prefect-server.git
          cache_root: /home/stoat/.cache/mps/source
          directory: my-prefect-server
    work_pool:
      name: home-pool
      job_variables:
        command: >-
          uv run
          --with 'my-prefect-server @ git+https://github.com/zzstoatzz/my-prefect-server.git{{ $MPS_PIN }}'
          prefect flow-run execute
        env:
          MPS_PIN: "{{ $MPS_PIN }}"
    schedules:
      - cron: "37 * * * *"
        active: true
```

Use one explicit release SHA for both installed package and retrieved source.
The implementation commit must be available remotely before updating diagnostics.
Run deployment validation and inspect the stored deployment to verify its exact
pin, custom pull step, home-pool assignment, and preserved hourly schedule.

Diagnostics trial completion criteria:

1. Cold run fetches exactly the requested commit and successfully imports the flow.
2. Repeated runs report a hit and perform no code fetch, including with the source
   remote unavailable after warming the cache.
3. Concurrent cold runs share one fetch but have independent writable directories.
4. A new pin retrieves new content; a rollback pin returns its old content.
5. Pruning an archive cannot invalidate a run that already materialized its source.
6. Missing commits, interrupted fetches, and corrupt archives never cause execution
   of stale or partially written source. A corrupt entry is rebuilt or fails clearly.
7. Logs distinguish hit/miss, fetched SHA, source bytes, materialization time, and
   pruning. Measure cold versus warm network activity during the diagnostics trial.

Only after diagnostics succeeds should other deployments opt in. Rollback is
removing diagnostics' custom pull override so it inherits the existing shared pull; the cache is disposable and
no worker restart is required.

## Prototype evidence

An isolated prototype under `/tmp/prefect-pull-cache-spike/` used actual Git
repositories, a real `prefect.deployments.steps.core.run_step` call, filesystem
locking, independent extraction, and four concurrent OS processes. No mocks.

Passed locally with Prefect 3.8.2:

- Import and call through Prefect's fully qualified step dispatcher.
- Retrieve an older exact commit rather than the repository's latest commit.
- Warm hit succeeds after renaming the source repository out of reach.
- Mutating one run's files leaves another run's files unchanged.
- Cold miss with an unavailable remote fails without leaving a usable run directory.
- Four concurrent cold callers produce one miss and three hits.
- Pruning to two archives removes the first cached commit while an existing run
  using its already extracted files remains valid.
- A branch name is rejected.

The same checks were also run in a temporary directory on heavypad using its
existing worker environment (Prefect 3.7.2). This checks the host's actual Python,
filesystem locking, Git, and Prefect step dispatch. It is not a registered
deployment test, a production remote fetch test, or a finished implementation.
The prototype is intentionally small: production timeouts, archive validation,
logging, byte limits, and interrupted-process cleanup remain implementation work.

## References

- [Prefect custom deployment steps](https://docs.prefect.io/v3/how-to-guides/deployments/prefect-yaml#custom-deployment-steps): importable Python function, keyword arguments, dictionary outputs.
- [Prefect pull action](https://docs.prefect.io/v3/how-to-guides/deployments/prefect-yaml#the-pull-action): runs for each deployment execution and can be overridden per deployment.
- Local Prefect 3.8.2 `deployments/steps/core.py::run_step` and
  `flows.py::load_flow_from_flow_run`: import/call lifecycle and returned-directory handling.
- Local `cli/flow_run.py::execute`: creates a temporary run workspace.
- Existing `prefect.yaml`, `pyproject.toml`, `packages/mps/pyproject.toml`, and
  `scripts/validate_deployments.py`: installed package, source pin, and pull-step contracts.
- [Bandwidth investigation](../audits/2026-09-28-home-bandwidth.md).

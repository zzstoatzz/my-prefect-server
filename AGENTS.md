# repository instructions

## access and targets

- Operator credentials are in the gitignored root `.env`; `just` loads it.
  Do not infer missing access from an empty shell environment. Runtime flow
  credentials are Prefect Secret blocks, resolved by `mps.secrets_plugin` or
  loaded through `mps.blocks`. Never commit or print credential values.
- `just prefect` and `just kubectl` target production through `deploy/targets.toml`.
  `just prefect-legacy` explicitly targets the retiring server. Root
  `kubeconfig.yaml` still belongs to that legacy cluster; ambient OrbStack
  Kubernetes is not production. Never silently change global client profiles
  or Kubernetes context. `PREFECT_TARGET` selects a configured CLI target.
- Read `deploy/heavypad-prefect/README.md` before operating the home stack.
  Confirm current runtime and deployment ownership from live state; dated
  migration records in `docs/migrations/` are evidence, not standing policy.
- Missing kubeconfig must be recovered out of band. `just kubeconfig` depends
  on local Terraform state and is not a universal recovery path. Refresh it
  after a server restart. Check local operator notes for SSH access; do not
  commit host addresses or keys.

## development and releases

- Keep the primary checkout on `main`. Reconcile temporary branches/worktrees
  and remove them when finished; preserve unfinished work in recoverable
  archives or project docs.
- Land production source and configuration on `main` before rollout. Use
  immutable release pins and wheels, not abandoned branch checkouts.
- Use `just` recipes for established operations. Never use `pip` or `uv pip`;
  use `uv add`, `uv sync`, or `uv run --with`.
- Push GitHub first, then Tangled with `just push`; runtime installs use the
  mirror. Run `just hooks` once per clone and the appropriate checks from
  `just check` before shipping.
- Consult the [Prefect source](https://github.com/PrefectHQ/prefect) for API or CLI
  behavior. Python support starts at 3.13 so dbt flows can select a compatible interpreter.

## deployment contracts

- `prefect.heavypad.yaml` owns home-server deployments, schedules and triggers.
  `prefect.yaml` retains inactive original-server rollback definitions.
  Never enable two schedulers for the same effects or silently resume a
  deliberately paused deployment.
- Preserve the declared code-delivery mode: Git runs pull their source;
  wheel-backed runs with `pull: []` import dotted module entrypoints unless
  the worker supplies files. Read `docs/deployments-validation.md`.
- `just validate-deployments` checks static contracts, not artifact availability
  or runtime readiness. Verify the real worker and run after deployment.
- Preserve command-prefix Prefect settings: workers may override deployment
  environment values before launching a process.
- HeavyPad registration runs on the trusted host after green CI for the exact
  main revision. Follow the registration runbook rather than injecting home
  credentials into Spindle microVMs.
- For legacy Kubernetes workers, read the compatibility requirements in
  [deployment validation](docs/deployments-validation.md).

## retiring shared resources

Before removing a deployed service or externally consumed data, follow
[service retirement](docs/service-retirement.md). Obtain Nate's approval if a
consumer remains. Ordinary source-file refactoring does not require this procedure.

## workflow behavior

- Dagster owns hub dbt asset execution and metadata on HeavyPad. Prefect's
  transform flow submits and awaits Dagster; do not duplicate dbt execution.
- Shared DuckDB files use `STORAGE_VERSION 'v1.0.0'` for dbt compatibility.
- Every read-write open of `analytics.duckdb` holds the single-writer
  `analytics-duckdb-writer` lease through `mps.db._write_conn` or
  `mps.lock.analytics_write_slot`. Readers snapshot to `/tmp`.
- Network tasks use retries with delays `[2, 5, 10]` and jitter. Let transient
  errors reach the task engine; decide degraded versus failed at the join.
- A state's name is independent of its type. Use a named `Completed` state
  when work completed with an unavailable upstream; see `docs/prefect-patterns.md`.
- Direct flow execution can use different storage and credentials from the
  production worker. Use a deployment run to verify production behavior.

## tooling and documentation

- Inspect the MCP tools in `plugins/mps/` before inventing a new client or
  claiming a capability is unavailable. PDSX supports record CRUD; the Prefect
  MCP is read-only. Mutations use the explicitly targeted CLI.
- Cost snapshots have known coverage and attribution gaps. Prefer declared
  ownership and prefix-match service names; see `docs/costs.md`.
- Keep README focused on purpose, setup and use. Operations and design belong
  in `docs/` or beside their deployment. Incidents go in `docs/incidents/`;
  shipped design notes and build logs go in `docs/archive/`.

---
name: deployments
description: Register, change, or debug Prefect deployments in this repo — schedules, triggers, parameters, work pools, job variables, secret injection, or dependency/python-version problems in flow runs. Use when editing prefect.yaml or when a flow run fails during pull or dependency install.
---

# deployments

`prefect.yaml` is the **source of truth** for schedules, triggers, parameters,
and per-deployment job variables. After any dependency or flow change:

```bash
just prefect deploy --all
```

## flow code is never baked in

It is pulled at runtime. The `pull:` steps clone `main` fresh per run:

```yaml
pull:
  - prefect.deployments.steps.run_shell_script:
      script: |
        bash -c "rm -rf my-prefect-server && (git clone --depth 1 --branch main https://tangled.sh/zzstoatzz.io/my-prefect-server.git my-prefect-server || git clone --depth 1 --branch main https://github.com/zzstoatzz/my-prefect-server.git my-prefect-server)"
  - prefect.deployments.steps.set_working_directory:
      directory: my-prefect-server
```

Two consequences that matter constantly:

- **Flow code never goes in worker images or ConfigMaps.** Don't put it there.
- **Prod runs pushed `main`, not your working tree.** An uncommitted change is
  not live, and a commit that isn't pushed is not live either. Conversely, a
  push changes prod behavior on the next run with no deploy step.

## dependencies install per run

The `command` job variable builds an ephemeral venv *before* pull steps run:

```yaml
command: >-
  uv run
  --refresh-package my-prefect-server
  --with 'my-prefect-server @ git+https://github.com/zzstoatzz/my-prefect-server.git'
  prefect flow-run execute
```

Per-deployment overrides go in `work_pool.job_variables.command`, **not** at the
deployment root. Example: `--python 3.13` for dbt compatibility.
`requires-python` is `>=3.13` (not 3.14) precisely so the transform flow can run
dbt under 3.13.

Heavy extras are opt-in: the `atlas` extra (numpy/scipy/umap/hdbscan/numba,
~250MB) installs via `my-prefect-server[atlas] @ git+...` only for `phi-atlas`,
so other flows don't pull ML libs they never use. `numba>=0.60` has a floor for a
real reason — without it uv backtracks numba to a py<3.10 sdist that crashed
`phi-atlas` daily from 2026-06-22.

## secrets

Flow *runtime* secrets (`ANTHROPIC_API_KEY`, `TURBOPUFFER_API_KEY`,
`CLOUDFLARE_API_TOKEN`, `TURSO_*`) are **not** in `.env`. They are Prefect Secret
blocks, injected into flow runs via `job_variables.env` in `prefect.yaml`,
resolved at `prefect deploy` time. Flow code does not call the Secret API for
these.

The exception is a block whose *shape* matters to the flow — e.g.
`hetzner-tokens` is a dict of `{label: token}`, one per Hetzner Cloud project,
because Hetzner has no account-wide token. That one is loaded in-flow via
`Secret.load(...)`, since an env var can't express a mapping.

## work pools

Two pools are defined as YAML anchors, `*home` and `*k8s`.

**`home-pool` (process, on heavypad) is where flow execution actually happens.**
Process flow runs execute in an ephemeral `/tmp` dir, so persistent analytics
paths **must** be set explicitly via `job_variables.env`:

```yaml
ANALYTICS_DB_PATH: /home/stoat/prefect-analytics/analytics.duckdb
LLM_SPEND_LOG_PATH: /home/stoat/prefect-analytics/llm-spend.jsonl
PREFECT_LOCAL_STORAGE_PATH: /home/stoat/prefect-analytics/storage
```

These are hostPaths on the home box, not a k8s PVC.

`kubernetes-pool` is a retained-but-unused fallback — no k8s worker runs in
normal operation. If you ever revive it: the base job template defaults
namespace to `default` and **must** be `prefect`; the worker image must be the
`-kubernetes` tag (`prefecthq/prefect:3-python3.14-kubernetes`, which has uv
pre-installed); and `PREFECT_INTEGRATIONS_KUBERNETES_OBSERVER_NAMESPACES=prefect`
is what makes namespace-scoped RBAC work.

## conventions

- Never `pip` or `uv pip` — `uv add`, `uv sync`, `uv run`.
- We maintain prefect-dbt; never replace `PrefectDbtOrchestrator` with subprocess
  calls.
- Prefect docs are on disk at `~/github.com/prefecthq/prefect/docs` — read before
  guessing.

## related

Where runs execute: [[home-pool-worker]]. Running one by hand: [[adhoc-runs]].

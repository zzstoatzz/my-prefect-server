this file is a set of notes for us:

- never use `pip` or `uv pip` — use `uv add`, `uv sync`, or `uv run --with`
- use `jq` for JSON processing, not python
- prefect docs are on disk at `~/github.com/prefecthq/prefect/docs` — read before guessing
- use justfile recipes instead of ad-hoc commands
- push to both remotes: `origin` (tangled.org) and `github` (github mirror)
- after server restart, re-fetch kubeconfig with `just kubeconfig`
- flow code never goes in worker images or ConfigMaps — it's pulled at runtime via `git_clone`
- worker image is `prefecthq/prefect:3-python3.14-kubernetes` (the `-kubernetes` tag matters; uv is pre-installed)
- `PREFECT_INTEGRATIONS_KUBERNETES_OBSERVER_NAMESPACES=prefect` is what makes namespace-scoped RBAC work
- kubernetes work pool base job template defaults namespace to `default` — must be `prefect`
- flow pods install deps via `uv run --with 'my-prefect-server @ git+https://github.com/...'` in the `command` job variable — this creates an ephemeral venv before pull steps run
- per-deployment overrides (e.g. `--python 3.13` for dbt compat) go in `work_pool.job_variables.command`, not at the deployment root
- requires-python is >=3.13 (not 3.14) so the enrich flow can run dbt under python 3.13
- we maintain prefect-dbt — never suggest replacing PrefectDbtOrchestrator with subprocess calls

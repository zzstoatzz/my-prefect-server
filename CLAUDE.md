this file is a set of notes for us:

- never use `pip` or `uv pip` — use `uv add`, `uv sync`, or `uv run --with`
- use `jq` for JSON processing, not python
- prefect docs are on disk at `~/github.com/prefecthq/prefect/docs` — read before guessing
- use justfile recipes instead of ad-hoc commands
- push to both remotes: `origin` (tangled.org) and `github` (github mirror)
- after server restart, re-fetch kubeconfig with `just kubeconfig`
- flow code never goes in worker images or ConfigMaps — it's pulled at runtime via `git_clone`
- worker image is `prefecthq/prefect:3-python3.11-kubernetes` (the `-kubernetes` tag matters)
- `PREFECT_INTEGRATIONS_KUBERNETES_OBSERVER_NAMESPACES=prefect` is what makes namespace-scoped RBAC work
- kubernetes work pool base job template defaults namespace to `default` — must be `prefect`

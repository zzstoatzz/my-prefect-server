this file is a set of notes for us:

## credentials — read this before assuming you have no access

- local tooling secrets live in `.env` at the repo root (gitignored, never committed). copy `.env.example` and fill it in. the justfile has `set dotenv-load`, so every `just` recipe picks `.env` up automatically — you do NOT have "no credentials" just because nothing is exported in your shell.
- `.env` keys: `HCLOUD_TOKEN`, `POSTGRES_PASSWORD`, `AUTH_STRING`, `DOMAIN`, `LETSENCRYPT_EMAIL` (+ optional `GRAFANA_DOMAIN`). the live server is `DOMAIN=prefect-server.waow.tech`.
- `AUTH_STRING` (`user:pass`) is the Prefect API admin credential. Prefect's own BasicAuth is disabled (`/api/ui-settings` returns `auth: null`); Traefik enforces auth instead — it lets public GETs through (read-only UI) but gates all write/POST endpoints. so unauthenticated `flow_runs/filter`, `logs/filter`, etc. return `Unauthorized` — you need `AUTH_STRING` to query runs/logs.
- query the live server with `just prefect <args>` (e.g. `just prefect flow-run ls`); it injects `PREFECT_API_URL`/`PREFECT_API_AUTH_STRING` from `.env`. raw API: `curl -H "Authorization: Basic $(printf "$AUTH_STRING" | base64)" https://$DOMAIN/api/...`.
- flow *runtime* secrets (`ANTHROPIC_API_KEY`, `TURBOPUFFER_API_KEY`, `CLOUDFLARE_API_TOKEN`, `TURSO_*`) are NOT in `.env` — they're Prefect Secret blocks, injected into flow pods via `job_variables.env` in `prefect.yaml`, resolved at `prefect deploy` time. flow code never touches the Secret API directly.

## conventions

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

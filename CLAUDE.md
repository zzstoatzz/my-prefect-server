this file is a set of notes for us:

## credentials — read this before assuming you have no access

- local tooling secrets live in `.env` at the repo root (gitignored, never committed). copy `.env.example` and fill it in. the justfile has `set dotenv-load`, so every `just` recipe picks `.env` up automatically — you do NOT have "no credentials" just because nothing is exported in your shell.
- `.env` keys: `HCLOUD_TOKEN`, `POSTGRES_PASSWORD`, `AUTH_STRING`, `DOMAIN`, `LETSENCRYPT_EMAIL` (+ optional `GRAFANA_DOMAIN`). the live server is `DOMAIN=prefect-server.waow.tech`.
- `AUTH_STRING` (`user:pass`) is the Prefect API admin credential. The Zig server's Prefect-compatible BasicAuth is enabled via the `prefect-auth` secret; unauthenticated `/api/*` calls return `Unauthorized`, while `/ui-settings` reports `auth: "BASIC"` for the bundled Prefect v2 UI login flow. Use `AUTH_STRING` for CLI/API queries.
- query the live server with `just prefect <args>` (e.g. `just prefect flow-run ls`); it injects `PREFECT_API_URL`/`PREFECT_API_AUTH_STRING` from `.env`. raw API: `curl -H "Authorization: Basic $(printf "$AUTH_STRING" | base64)" https://$DOMAIN/api/...`.
- flow *runtime* secrets (`ANTHROPIC_API_KEY`, `TURBOPUFFER_API_KEY`, `CLOUDFLARE_API_TOKEN`, `TURSO_*`) are NOT in `.env` — they're Prefect Secret blocks, injected into flow runs via `job_variables.env` in `prefect.yaml`, resolved at `prefect deploy` time. flow code never touches the Secret API directly.

## cluster access

- the prod stack runs on a single-node k3s cluster in the `prefect` namespace. the local orbstack k8s context is not prod — verify context before acting.
- kubectl uses `kubeconfig.yaml` at the repo root (gitignored). the justfile exports `KUBECONFIG := source_directory()/kubeconfig.yaml`, so `just status` / `just logs` / deploy recipes target prod only once that file exists.
- if `kubeconfig.yaml` is missing, restore it out-of-band rather than relying on ambient kubectl context. `just kubeconfig` currently depends on local terraform state, which may not be present.
- node SSH access exists out-of-band. avoid committing host/IP/key details; check local operator notes or ask the user when needed.

## conventions

- never use `pip` or `uv pip` — use `uv add`, `uv sync`, or `uv run --with`
- use `jq` for JSON processing, not python
- prefect docs are on disk at `~/github.com/prefecthq/prefect/docs` — read before guessing
- use justfile recipes instead of ad-hoc commands
- push to configured remotes. This repo currently has `origin` (tangled.org);
  if a `github` mirror remote is present in a checkout, push that too.
- after server restart, re-fetch kubeconfig with `just kubeconfig`
- **flow execution runs on the `home-pool` *process* worker on the home box (heavypad)** — a systemd unit polling the server outbound over Tailscale (`deploy/home-worker/`). the `kubernetes-pool` bullets below describe the retained-but-unused k8s fallback path (no k8s worker runs in normal operation), and the analytics paths are hostPaths under `/home/stoat/prefect-analytics`, not a k8s PVC.
- flow code never goes in worker images or ConfigMaps — it's pulled at runtime via `git_clone`
- worker image is `prefecthq/prefect:3-python3.14-kubernetes` (the `-kubernetes` tag matters; uv is pre-installed)
- `PREFECT_INTEGRATIONS_KUBERNETES_OBSERVER_NAMESPACES=prefect` is what makes namespace-scoped RBAC work
- kubernetes work pool base job template defaults namespace to `default` — must be `prefect`
- flow pods install deps via `uv run --with 'my-prefect-server @ git+https://github.com/...'` in the `command` job variable — this creates an ephemeral venv before pull steps run
- per-deployment overrides (e.g. `--python 3.13` for dbt compat) go in `work_pool.job_variables.command`, not at the deployment root
- requires-python is >=3.13 (not 3.14) so the transform flow can run dbt under python 3.13
- we maintain prefect-dbt — never suggest replacing PrefectDbtOrchestrator with subprocess calls

## agent tooling — MCP surfaces and what they actually do

the `mps` plugin in `plugins/mps/` bundles two MCP servers. know what each one
can do before hand-rolling `curl`, and before claiming something isn't possible.

- **pdsx** (`https://pdsx-by-zzstoatzz.fastmcp.app/mcp`) — full PDS record
  **CRUD**: `list_records`, `get_record`, `describe_repo`, `query`, `whoami`,
  and `create_record` / `update_record` / `delete_record`. it is **not
  read-only**. use it for cost snapshots, phi docket, and any
  `io.zzstoatzz.*` record work instead of resolving handle → DID → PDS by hand.
  the only CLI carve-outs are blob upload (`pdsx upload-blob` — base64 through a
  JSON-RPC boundary is the wrong shape), batch JSONL ops with concurrency, and
  permissioned spaces. source: `~/github.com/zzstoatzz/pdsx`.
- **prefect** (`uvx --from prefect-mcp prefect-mcp-server`) — read-only
  diagnostics. this one genuinely is read-only, but that's a *scope* decision
  about a very large API, not a principle about MCP. do not generalize it to
  other servers. mutations go through `just prefect ...`.

if you are about to say "the MCP can't do X" or offer to build a missing tool,
grep its tool list first. that has been wrong more than once.

## running flows ad hoc

- Flow files under `flows/` are ordinary Python modules. Most have an
  `if __name__ == "__main__"` entrypoint, so they can be run directly for
  local/debug execution, e.g. `uv run python flows/diagnostics.py` or
  `uv run python flows/phi_atlas.py --dry-run`.
- Direct local execution is useful for debugging pure Python behavior, but be
  careful with write paths: default local storage may be `/tmp`, not the
  production analytics dir (`/home/stoat/prefect-analytics` on the home box), and flow code that calls `Secret.load(...)` still
  needs `PREFECT_API_URL`/`PREFECT_API_AUTH_STRING` pointed at the live server.
- To exercise the real production worker (home-pool), pull steps, job variables,
  and injected secret-block values, run deployments through the
  Prefect API: `just prefect deployment run 'diagnostics/diagnostics' --watch`
  or `just prefect deployment run 'ingest/ingest' --watch`.
- `prefect.yaml` is the source of truth for deployment schedules, triggers,
  parameters, and per-deployment job variables. Re-register with
  `just prefect deploy --all` after dependency or flow changes.

# operations

standing the system up, and the recipes that run it day to day. every command
is a `justfile` recipe; `just --list` is the full surface. `just` loads `.env`,
so nothing here needs variables exported in the shell.

## credentials

copy `.env.example` to `.env` and fill in `HCLOUD_TOKEN`, `POSTGRES_PASSWORD`,
`AUTH_STRING`, `DOMAIN`, `LETSENCRYPT_EMAIL`, and optionally `GRAFANA_DOMAIN`.

- `AUTH_STRING` (`user:pass`) is the Prefect API admin credential. the server
  enforces basic auth on `/api/*`; `just prefect <args>` injects it for the CLI.
- flow runtime secrets (`ANTHROPIC_API_KEY`, `TURBOPUFFER_API_KEY`,
  `CLOUDFLARE_API_TOKEN`, `TURSO_*`) are Prefect Secret blocks, not `.env`.
  `prefect.yaml` references them as `prefect-block://` values in
  `job_variables.env`, resolved when a run starts. flows that load blocks
  explicitly use the typed helpers in `mps.blocks`.
- `kubeconfig.yaml` at the repo root (gitignored) is what `kubectl` and every
  cluster recipe use. `just kubeconfig` fetches it after the server boots.

## the control plane (hetzner VM)

```sh
just init              # terraform init
just infra             # create the VM with k3s
just kubeconfig        # wait for k3s, fetch kubeconfig.yaml
just deploy            # cert-manager, prefect-server (zig chart), postgres + redis, monitoring, dashboards
just storage           # analytics hostPath + results PVC, kubernetes-pool base job template
```

then point DNS at `just server-ip`: `$DOMAIN`, `$GRAFANA_DOMAIN` (default
`prefect-metrics.waow.tech`), and `hub.waow.tech`.

the prefect server is the zig port at
[prefect-server](https://tangled.org/zzstoatzz.io/prefect-server), built on
the node and imported into k3s by `just publish-server-remote`. after a server
deploy, `just verify-deploy` waits for a flow run to *complete*, because pods
Running and `/health` 200 have both been true while every run crashed.

## the worker (heavypad)

most flow execution runs on the home box as a systemd process worker for
`home-pool`, polling the server outbound over Tailscale. no ingress, no
port-forward. the installer and unit are in [deploy/home-worker/](../deploy/home-worker/);
[deploy/hub-data-sync/](../deploy/hub-data-sync/) rsyncs `hub.duckdb` and
`llm-spend.jsonl` to the VM every few minutes so the hub serves fresh data
without a round trip home.

`phi-sprites-spike` runs selected gardener jobs in Sprites. Its supervisor
and local wheel artifacts live on Heavypad; see the deployment declarations
and [validation contracts](deployments-validation.md).

`kubernetes-pool` survives only as a defined fallback. its base job template
is applied by `just storage`; no k8s worker runs in normal operation.

```sh
just heavypad-status   # installed toolchain, worker unit, disk, codex login expiry
```

### disk on heavypad

Measured 2026-09-19: 878 GB used of 1.8 TB. What holds it, who cleans it, and
who a cleanup touches:

| path | size | owner | retention | affected by cleanup |
| --- | --- | --- | --- | --- |
| `~/typeahead-index/build/build-*` | 446 GB (37 builds) | `typeahead-index` | **the flow prunes after each publish** (since 2026-09-19): keeps the 2 newest plus whatever `typeahead.waow.tech/health/freshness` reports as serving; prunes nothing when that endpoint is unreachable | only that flow; the search service serves from R2. A person running an offline differential against an older build must copy it first |
| `~/typeahead-plc/weekly/*.jsonl.gz` | 51 GB (200 weeks) | `typeahead-plc-identity` | none; the whole PLC bundle history stays | the flow itself: `--after` is computed from the newest bundle, and a full re-derive needs every week. Do not prune by hand |
| `~/stream-gate`, `~/github.com`, `~/data` | 43 / 28 / 11 GB | stream, checkouts, misc | none | stream's gate state; clones other flows reuse |
| `~/.cache/uv` | **115 GB** (100 GB in `archive-v0`, 8,458 envs; `diagnostics` reports the count hourly) | every flow, since each run installs from git | none; `uv cache prune` is safe (drops unreferenced entries only) | every home-pool flow's next start is slower |
| `~/.cache/llama.cpp/models` | 35 GB: gemma-4-12b-it, Qwen3.5-9B, Qwen3.6-35B-A3B (all Q4_K_M, fetched 2026-08-26; `server.log` shows a CPU-only llama-server, no CUDA) | a hand-run inference experiment | none | nobody; re-downloadable |
| `~/.cache/huggingface/hub` | 32 GB: FLUX.1-dev, untouched since 2024-08-19 | an old experiment | none | nobody; safe to delete |
| `/tmp` | 11 GB, 771 entries; three fixed-name 2.6 GB duckdb snapshots (`brief`, `compact`, `ingest` overwrite theirs each run), an `iroh-mcp-smoke` dir, a leaflet build | flows and hand runs | Ubuntu cleans `/tmp` only at boot (`D /tmp 1777 root root -`), and the box has been up 22 days | a flow mid-run if its snapshot is removed underneath it |

The index builds were the only thing growing fast (about 14 GB every 3 days).
The rest is a one-time ~180 GB of caches and stale experiments (uv archive,
FLUX, the llama models if not wanted); `uv cache prune` and `rm` by hand,
between flow runs. Revisit when `diagnostics` shows disk free trending down.

## flows

`prefect.yaml` owns schedules, triggers, tags, parameters, and per-deployment
job variables. every push to `main` registers all of them through
`.tangled/workflows/deploy.yml`. Git-backed jobs using MPS_PIN track the pushed
commit; explicit legacy pins and wheel routes retain their declared versions.
The static [validator](deployments-validation.md) runs before registration. The inventory in
[deployments.md](deployments.md) is generated from the same file and CI fails
if it is stale.

```sh
just check                                       # what CI runs before it deploys
just inventory                                   # regenerate docs/deployments.md
MPS_PIN="@$(git rev-parse HEAD)" just validate-deployments --release # check the intended pin
# Use the same MPS_PIN with just prefect deploy for manual registration.
just prefect deployment run 'diagnostics/diagnostics' --watch   # exercise the real worker
just automations                                 # apply deploy/automations.yaml (idempotent)
just work-pool                                   # apply deploy/work-pools templates
```

standalone automations (send-notification, cross-deployment triggers) live in
`deploy/automations.yaml` because `prefect.yaml` can only express
run-deployment triggers bound to a deployment.

## day to day

```sh
just health            # /api/health
just status            # node + pod resource usage
just logs              # tail prefect-server; `just logs <component>` for others
just prefect flow-run ls
just ssh               # the VM
just dashboards        # reload grafana from deploy/dashboards/
```

## the hub

```sh
just publish-web-remote   # build the sveltekit image on the node, import into k3s, roll the pod
```

`just web` is the older local docker build-and-push path. the hub itself is
described in [hub.md](hub.md).

## analytics

```sh
just init-analytics    # first time: dbt deps, seed, compile
```

`analytics.duckdb` is single-writer. every read-write open holds the
`analytics-duckdb-writer` global concurrency limit through
`mps.lock.analytics_write_slot`; readers snapshot the file instead of locking.

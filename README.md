personal data pipeline and intelligence layer. digests github, [tangled.org](https://tangled.org), and bluesky activity, scores items, generates LLM-curated briefings, and maintains phi's long-term memory. self-hosted across two machines joined by Tailscale: a hetzner VM (k3s) runs the prefect control plane and serves the public edge, while **all flow execution runs on a home Linux box** that polls outbound — the home box does the compute, the VM serves the bytes.

[hub](https://hub.waow.tech) · [grafana](https://prefect-metrics.waow.tech/d/executive-overview/executive-overview?orgId=1&from=now-6h&to=now&timezone=browser)

```
                        where it runs
  ─────────────────────────────────────────────
   home box ("heavypad")                       hetzner VM (k3s, EU)
   i9 · 24c · 64GB · 1TB SSD + 2TB NVMe        the public edge + control plane
   ──────────────────────────────────         ────────────────────────────────
   • home-pool worker runs ALL flow            • prefect server (control plane)
     execution — outbound poll, no ingress     • hub.waow.tech + grafana (public, TLS)
   • owns the analytics DuckDB +               • serves bytes, not compute
     llm-spend.jsonl (the source of truth)

   home box ──► rsync hub.duckdb + llm-spend.jsonl every 3m, over tailscale ──► VM
   so the edge serves fresh data WITHOUT shipping heavy pages from home (a page
   from home behind the EU edge meant a US→EU→US round trip; the data is ~4MB).

   principle: home = compute & state factory; VM = public edge & bandwidth.
   relays, the PDS, and user-facing products stay cloud.
```

```
                        data sources
  ─────────────────────────────────────────────
  github API        ──┐
  tangled PDS       ──┤
  bluesky likes     ──┼──► ingest (hourly) ──► DuckDB
  phi memory (tpuf) ──┘
                                                  │
                                                  ▼
                                          transform (dbt)
                                          [on ingest ✓]
                                                  │
                        ┌─────────────────────────┼──────────┐
                        ▼                         ▼          ▼
                      brief                    compact    hub UI
                  [on transform ✓]         [on transform ✓]
                        │                         │
                        ▼                         ▼
                  briefing.json              TurboPuffer
                                             (phi-users-*)

                        phi identity flows
  ─────────────────────────────────────────────
  morning (daily 8am CT) ──► TurboPuffer
          │
          ▼
  curate  ─────────────────► Semble API
  phi-atlas (daily 8am CT) ─► PDS atlas blob
          │
          ▼
  docket  ─────────────────► PDS docket blob

                        publication flows
  ─────────────────────────────────────────────
  rebuild-atlas (every 6h) ──► Cloudflare Pages
  pds-records (ad hoc) ──► PDS record maintenance
```

see [docs/hub.md](docs/hub.md) for the full pipeline breakdown.

<details>
<summary>deployment</summary>

### prerequisites

- [terraform](https://developer.hashicorp.com/terraform/install)
- [just](https://just.systems)
- [uv](https://docs.astral.sh/uv) (Python 3.13+; the worker image is Python 3.14, while dbt runs under a per-deployment Python 3.13 override)
- a hetzner cloud API token
- a domain with DNS you control

### setup

```bash
cp .env.example .env
# fill in: HCLOUD_TOKEN, POSTGRES_PASSWORD, AUTH_STRING, DOMAIN, LETSENCRYPT_EMAIL
uv sync                # install workspace (mps + root)
```

### deploy

```bash
just init              # terraform init
just infra             # create the VM
just kubeconfig        # wait for k3s, fetch kubeconfig
just deploy            # cert-manager, prefect server, monitoring, dashboards
just worker            # deploy the kubernetes worker
just storage           # create analytics hostPath + results PVC, apply work pool template
```

### home worker (where flows actually run)

The steps above stand up the control plane + public edge on the VM. Flow
**execution** runs on the home box via a systemd `home-pool` worker that polls
the server outbound over Tailscale (no ingress, no port-forward) — see
[deploy/home-worker/](deploy/home-worker/). Deployments target `home-pool`; the
k8s worker (`just worker`) remains as a fallback pool. The hub reads analytics
synced from the home box every few minutes — see
[deploy/hub-data-sync/](deploy/hub-data-sync/).

Zig server adoption/migration details live in
[notes/zig-prefect-server-migration.md](notes/zig-prefect-server-migration.md).

after `deploy`, point your DNS:
- `$DOMAIN` → server IP (`just server-ip`)
- `$GRAFANA_DOMAIN` → same IP (default: `prefect-metrics.waow.tech`)
- `hub.waow.tech` → same IP

### verify

```bash
just health               # curl the /api/health endpoint
just status               # node + pod resource usage
just prefect work-pool ls
```

### operations

```bash
just logs                 # tail prefect-server logs (default)
just logs worker          # tail worker logs
just prefect flow-run ls  # run any prefect CLI command remotely
just dashboards           # reload grafana dashboards from deploy/dashboards/
just ssh                  # ssh into the server
```

flow deployments are registered automatically on every push to main via `.tangled/workflows/deploy.yml`.

### hub (sveltekit frontend)

```bash
just publish-web-remote    # build hub on the Hetzner node + deploy hub.waow.tech
```

`just web` still exists as the legacy local Docker build/push path, but normal
operations use `publish-web-remote` so linux/amd64 images are built on the
node and imported into k3s directly.

### analytics (dbt + duckdb)

```bash
just init-analytics   # first-time: dbt deps, seed, compile
```

</details>

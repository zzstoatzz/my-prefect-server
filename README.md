self-hosted prefect OSS on a single hetzner VM (k3s), with monitoring.

[executive dashboard](https://prefect-metrics.waow.tech/d/executive-overview/executive-overview?orgId=1&from=now-6h&to=now&timezone=browser) · [hub](https://hub.waow.tech)

<details>
<summary>deployment</summary>

### prerequisites

- [terraform](https://developer.hashicorp.com/terraform/install)
- [just](https://just.systems)
- [uv](https://docs.astral.sh/uv) (Python 3.14+)
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
just storage           # create analytics hostPath + results PVC, patch work pool
```

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
just web    # build + push + deploy hub.waow.tech
```

### analytics (dbt + duckdb)

```bash
just init-analytics   # first-time: dbt deps, seed, compile
```

</details>

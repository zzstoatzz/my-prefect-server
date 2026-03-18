self-hosted prefect OSS on a single hetzner VM (k3s), with monitoring.

[executive dashboard](https://prefect-metrics.waow.tech/d/executive-overview/executive-overview?orgId=1&from=now-6h&to=now&timezone=browser)

<details>
<summary>deployment</summary>

### prerequisites

- [terraform](https://developer.hashicorp.com/terraform/install)
- [just](https://just.systems)
- [uv](https://docs.astral.sh/uv)
- a hetzner cloud API token
- a domain with DNS you control

### setup

```bash
cp .env.example .env
# fill in: HCLOUD_TOKEN, POSTGRES_PASSWORD, AUTH_STRING, DOMAIN, LETSENCRYPT_EMAIL
```

### deploy

```bash
just init              # terraform init
just infra             # create the VM
just kubeconfig        # wait for k3s, fetch kubeconfig
just deploy            # cert-manager, prefect server, monitoring, dashboards
just worker            # deploy the kubernetes worker
just prefect-init      # terraform init for prefect resources
just prefect-apply     # create work pool + variables
just register-flows    # register flow deployments from code
```

after `deploy`, point your DNS:
- `$DOMAIN` → server IP (`just server-ip`)
- `$GRAFANA_DOMAIN` → same IP

### verify

```bash
just health            # curl the /api/health endpoint
just status            # node + pod resource usage
just prefect work-pool ls
```

### operations

```bash
just logs              # tail prefect-server logs (default)
just logs worker       # tail worker logs
just prefect flow-run ls  # run any prefect CLI command remotely
just dashboards        # reload grafana dashboards from deploy/dashboards/
just ssh               # ssh into the server
```

</details>

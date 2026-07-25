---
name: server-access
description: Query or inspect the self-hosted Prefect server at prefect-server.waow.tech, or the k3s cluster it runs on. Use when you need flow-run/deployment/block state, are told you have no credentials, are unsure which kubectl context is prod, or an /api/ call returns Unauthorized.
---

# server access

## you probably do have credentials

Nothing is exported in the shell, and that is not the same as having no access.
Local tooling secrets live in `.env` at the repo root (gitignored). The justfile
has `set dotenv-load`, so **every `just` recipe picks `.env` up automatically**.

Keys: `HCLOUD_TOKEN`, `POSTGRES_PASSWORD`, `AUTH_STRING`, `DOMAIN`,
`LETSENCRYPT_EMAIL`, optional `GRAFANA_DOMAIN`. Live server is
`DOMAIN=prefect-server.waow.tech`.

If `.env` is missing, copy `.env.example` and fill it in — do not conclude the
task is blocked.

## querying the server

Prefer the recipe; it injects both env vars for you:

```bash
just prefect flow-run ls
just prefect deployment ls
just prefect block ls
```

It expands to:

```bash
PREFECT_API_URL="https://$DOMAIN/api" PREFECT_API_AUTH_STRING="$AUTH_STRING" \
    uv run --with prefect prefect <args>
```

Raw API when the CLI doesn't expose what you need:

```bash
curl -H "Authorization: Basic $(printf "$AUTH_STRING" | base64)" \
  "https://$DOMAIN/api/..."
```

`AUTH_STRING` is `user:pass` and is the admin credential. The Zig server's
Prefect-compatible BasicAuth comes from the `prefect-auth` secret —
unauthenticated `/api/*` returns `Unauthorized`, and `/ui-settings` reports
`auth: "BASIC"` for the bundled Prefect v2 UI login flow.

## CLI output is agent-hostile by default

Rich tables truncate IDs and names into uselessness, and partial UUIDs are not
accepted anywhere. Get full values as JSON:

```bash
just prefect api POST /flow_runs/filter --data '{"limit": 5}'
just prefect flow-run inspect <uuid> -o json
```

Also: `--flow-name` on `flow-run ls` does **not** reliably filter — it will
return unrelated flows. Filter via `POST /flow_runs/filter` instead of trusting
the flag.

Use `prefect --no-prompt ...` for anything that would otherwise ask for
confirmation.

## the cluster is not orbstack

The prod stack is a single-node k3s cluster, namespace `prefect`. A local
orbstack k8s context is **not** prod — verify context before acting.

kubectl reads `kubeconfig.yaml` at the repo root (gitignored); the justfile
exports `KUBECONFIG := source_directory()/kubeconfig.yaml`, so `just status` /
`just logs` target prod only once that file exists.

```bash
just status
just logs                    # defaults to component=prefect-server
just logs component=<name>
just health
```

If `kubeconfig.yaml` is missing, restore it out-of-band rather than falling back
to ambient kubectl context. `just kubeconfig` depends on local terraform state,
which may not be present. Re-fetch it after a server restart.

Node SSH access exists out-of-band. Do not commit host/IP/key details.

## related

Flow execution does not happen here — see [[home-pool-worker]]. Registering
deployments is [[deployments]].

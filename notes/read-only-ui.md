# read-only public UI: thinking it through

## what we want

anyone can open `prefect-server.waow.tech` and browse the UI, see flow runs, logs, deployments. they cannot trigger runs, delete anything, or write. admin users still have full access by supplying credentials.

## where the first plan broke down

the initial plan was: keep Prefect's own BasicAuth, use Traefik IngressRoute to inject admin credentials on the server's behalf for read-only routes, and require user-supplied credentials for write routes.

**this doesn't work.** here's why:

the Prefect UI is a browser SPA. one of its first calls is `GET /api/ui-settings`, which returns:

```json
{"api_url": "...", "csrf_enabled": false, "auth": "BASIC"}
```

when the UI sees `auth: "BASIC"`, it **shows a login prompt immediately**, regardless of what Traefik injects on the network level. the UI doesn't know Traefik is handling auth on its behalf — from the browser's perspective, the server says "I require BasicAuth" and shows the prompt. credential injection at the proxy layer is invisible to the SPA.

so even though all the data calls would succeed (because Traefik injects creds), the UI would still block public users with a login dialog. dead end.

## the correct architecture

to get a truly public UI, we have to **disable Prefect's built-in BasicAuth** and move all auth to Traefik:

```
                   ┌──────────────────────────────────────────────────────┐
internet ──→ Traefik IngressRoute                                          │
                   │  rule: GETs + safe read POSTs → no middleware        │
                   │  rule: everything else → BasicAuth middleware         │
                   └──────────────────┬───────────────────────────────────┘
                                      ↓
                              Prefect server (no auth)
```

with `basicAuth.enabled: false` in prefect-values.yaml, Prefect's `/api/ui-settings` returns `"auth": null`. the UI skips the login prompt and renders normally for public users.

admin users hit write endpoints → Traefik's BasicAuth middleware prompts for credentials → browser sends them → Traefik validates → passes request to Prefect.

## trade-off: loss of defense-in-depth

Prefect's own auth was a backstop — even if Traefik was bypassed (e.g., from within the cluster), the server required credentials. without it, any pod in the cluster can call the Prefect API directly with no auth.

in practice: the worker pod already calls Prefect directly as part of normal operation. the threat model for cluster-internal access is: a compromised worker or job pod could make write API calls. for this use case (personal infra, single-node k3s), this risk is acceptable.

## what Traefik actually needs

**not standard Ingress**: we drop `ingress.enabled: true` and instead use:

1. **a cert-manager `Certificate` CR** (not the Ingress annotation) — tells cert-manager to request a cert for the domain and store it in a named Secret. IngressRoute references that Secret. this is the clean, explicit alternative to the "keep Ingress for cert-manager" hack.

2. **Traefik `Middleware` CRDs**:
   - `prefect-admin-auth` (BasicAuth type) — requires htpasswd-format credentials in a k8s Secret
   - note: Traefik BasicAuth middleware expects `user:bcrypt_hash` (not plaintext `user:pass`). justfile needs to hash the password before creating the secret.

3. **Two Traefik `IngressRoute` CRDs** (priority-based):
   - `prefect-public` (priority 20): GETs + explicit safe POST list → no middleware
   - `prefect-admin` (priority 10): catch-all → `prefect-admin-auth` middleware

## things to check before implementing

- **k3s version on the server**: determines Traefik version (v2 = `traefik.containo.us/v1alpha1`, v3 = `traefik.io/v1alpha1`). run `kubectl version` or `k3s --version` to check.
- **is CSRF enabled?** default is false, but if someone enabled it, POST filter endpoints need CSRF tokens from public users. check with `curl https://$DOMAIN/api/ui-settings | jq .csrf_enabled`.
- **does the Prefect UI gracefully handle write-path 401s?** when a public user clicks "run deployment", Traefik returns 401. the browser shows a BasicAuth prompt (native browser dialog for 401 on XHR). this is... ok but not great UX. fine for now.
- **htpasswd availability**: macOS has `openssl passwd` available for generating bcrypt hashes. or use `htpasswd` if apache2-utils is installed.

## files to create/modify

| file | change |
|------|--------|
| `deploy/prefect-values.yaml` | `basicAuth.enabled: false`, `ingress.enabled: false` |
| `deploy/prefect-certificate.yaml` | new — cert-manager Certificate CR |
| `deploy/prefect-ingress-route.yaml` | new — Middleware + 2x IngressRoute |
| `justfile` | add recipe to create htpasswd secret + apply new yamls |

## what the safe POST allowlist looks like

explicit paths (no regex, no wildcards):
- `/api/flows/filter|count|paginate`
- `/api/flow_runs/filter|count|paginate|history|lateness`
- `/api/task_runs/filter|count|paginate|history`
- `/api/deployments/filter|count|paginate`
- `/api/artifacts/filter|count`, `/api/artifacts/latest/filter|count`
- `/api/work_pools/filter|count`, `/api/work_queues/filter`
- `/api/logs/filter`
- `/api/events/filter`, `/api/events/count-by/{day,event_type,resource_id}`
- `/api/variables/filter|count`
- `/api/automations/filter|count`
- `/api/block_types/filter`, `/api/block_schemas/filter`, `/api/block_documents/filter`
- `/api/concurrency_limits/filter`, `/api/v2/concurrency_limits/filter`

this is ~35 explicit paths. verbose but provably correct — if a path isn't on this list, it hits the catch-all admin route.

## open question: UX for write-blocked public users

when a logged-out user tries to run a deployment, they get a native browser BasicAuth dialog (401). this is... jarring. alternatives:
1. accept it — it's clear "this requires login"
2. add a custom error page in Traefik for 401s on write routes
3. modify the Prefect UI to handle 401 on write routes gracefully (fork territory)

option 1 is fine for now.

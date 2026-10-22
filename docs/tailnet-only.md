# tailnet-only prefect server

Goal: `prefect-server.waow.tech` (UI, `/api`, `/push`, `/api/presence`) answers
only to tailnet devices. The inventory was taken 2026-10-21; steps 1 and 2 are done.

## constraints

- The node is on the tailnet as `hetzner-edge`; its ingress already answers on
  the tailnet address with the right TLS name.
- Traefik on the same node also serves hosts that must stay public:
  `hub.waow.tech` (Cloudflare-proxied, behind Access, read by GitHub Actions and
  other repos) and `spindle.zzstoatzz.io` (tangled's appview dials it). Public
  80/443 therefore stay open; prefect-server is restricted per host, not by the
  Hetzner firewall.
- A tailnet-only host cannot answer HTTP-01, so its certificate must use the
  DNS-01 issuer (step 2).
- heavypad's prefect worker must keep running. The flip may take a short
  planned cutover window, nothing longer; heavypad's tailnet address is allowed
  before anything is restricted.

## steps

1. **close public ssh and k3s API.** Done 2026-10-21: the live firewall and
   `infra/main.tf` allow only 80/443 (there is no local terraform state, so the
   live rules were replaced with `hcloud firewall replace-rules`). kubeconfig and
   `just ssh` work over the tailnet (`tls-server-name: kubernetes`, since the k3s
   cert has no SAN for the tailnet address). Terraform does not install
   tailscale, so a rebuilt node is reachable only through the Hetzner console
   until it joins.
2. **DNS-01 for prefect-server.** Done 2026-10-21: `letsencrypt-dns01`
   (deploy/cluster-issuer.yaml) solves through Cloudflare with a token scoped to
   DNS Write + Zone Read on `waow.tech` (secrets store
   `cloudflare.cert_manager_dns01_waow_tech`, k8s `cert-manager/cloudflare-dns01`).
   prefect-server and hub both moved to it and re-issued. The solver stuck since
   July was hub's: hub is Cloudflare-proxied behind Access, so HTTP-01 got the
   Access page, and its origin certificate had expired on 2026-08-16.
3. **migrate consumers** (below), each verified against the live server.
4. **flip**, in two stages so heavypad never loses its path. First point the
   `prefect-server.waow.tech` A record at the tailnet address and wait out the
   TTL; nothing is restricted yet, so clients still holding the public address
   keep working. Once heavypad resolves the tailnet address and its workers
   heartbeat through it, add a Traefik `ipAllowList` on that host for the tailnet range
   plus the pod CIDR (needs `externalTrafficPolicy: Local` on Traefik's service
   so source addresses survive). Keeping the hostname keeps every hardcoded URL
   and Discord link working, and spindle CI guests, which cannot use MagicDNS,
   can still resolve it.
5. **verify** from off-tailnet (refused) and from the Mac, the phone, heavypad,
   and an in-cluster pod (served), then run a real deployment end to end.

## consumers

| consumer | runs on | today | change |
|---|---|---|---|
| home-pool worker, prefect-mcp-selfhosted, phi-inference, dagster | heavypad (tailnet) | public URL via public DNS | nothing beyond the DNS flip. Verified 2026-10-22: heavypad reaches the tailnet address with a valid certificate for the hostname (`curl --resolve`), and it resolves through tailscale0, so it follows the record without a hosts pin (which would need sudo) |
| `just prefect`, operator CLI (laptop worker removed 2026-10-22) | Mac (tailnet) | public URL | nothing; Tailscale must be connected |
| prefect-ios app, Safari | iPhone (tailnet) | `/api`, `/push`, events socket | Tailscale on-demand; Siri intents and notification actions need the VPN up |
| iPhone Shortcuts presence | iPhone, background | `POST /api/presence` | unreliable without VPN; move to `hub.waow.tech/api/presence` behind Cloudflare with a path bypass, or keep an exception |
| hub pod | in-cluster | done 2026-10-22: `presence-ingress/prefect-api-url` is `http://prefect-server.prefect.svc:4200/api`, verified with the hub's own credential | nothing at the flip |
| prefect-push | in-cluster | inbound `/push` from the app | follows the app onto the tailnet |
| spindle CI deploys (my-prefect-server `deploy.yml`, stream `admission.yml`) | heavypad microVMs | public URL | should route over heavypad's tailnet once DNS points there; verify one run |
| gardener-exe flow runs | exe.dev VMs (off tailnet) | worker env URL | tailscale is in the VM image; inject an ephemeral tagged auth key via `run_environment` |
| phi bot | Fly (off tailnet) | REST `/api/flow_runs/*` | tailscale on Fly with an ephemeral key, or an exception |
| hosted MCP `prefect-by-zzstoatzz.fastmcp.app` | SaaS (off tailnet) | headers carry the API URL | retire in favor of the heavypad MCP once phi is on the tailnet |
| evergreen probes | Cloudflare Worker | `/api/health` (unauthenticated), grafana | remove the prefect and grafana service URLs from `site/services.json` at the flip, not before; evergreen has no private flag |
| pub-search ingestion watchdog | GitHub Actions | done 2026-10-22: holds the read-only string (verified "builder ok"); prints "builder check skipped" when unreachable | nothing; it loses the cause-level check after the flip |
| phi-sprites-spike pool | Fly Sprites | unused pool | retire |

Open questions: the contents of `presence-ingress/prefect-api-url`; whether
heavypad overrides DNS today (the docs say the worker polls "over Tailscale",
but the hostname resolves to the public address); how phi on Fly reaches
heavypad today.

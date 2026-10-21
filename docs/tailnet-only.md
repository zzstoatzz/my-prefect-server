# tailnet-only prefect server

Goal: `prefect-server.waow.tech` (UI, `/api`, `/push`, `/api/presence`) answers
only to tailnet devices. The inventory was taken 2026-10-21; step 1 is done.

## constraints

- The node is on the tailnet as `hetzner-edge`; its ingress already answers on
  the tailnet address with the right TLS name.
- Traefik on the same node also serves hosts that must stay public:
  `hub.waow.tech` (Cloudflare-proxied, behind Access, read by GitHub Actions and
  other repos) and `spindle.zzstoatzz.io` (tangled's appview dials it). Public
  80/443 therefore stay open; prefect-server is restricted per host, not by the
  Hetzner firewall.
- cert-manager uses HTTP-01. Once the prefect-server record points at a tailnet
  address, Let's Encrypt cannot reach it, so that certificate needs DNS-01 with a
  Cloudflare token that can edit `waow.tech` records (the existing
  `cloudflare-api-token` block cannot see that zone).

## steps

1. **close public ssh and k3s API.** Done 2026-10-21: the live firewall and
   `infra/main.tf` allow only 80/443 (there is no local terraform state, so the
   live rules were replaced with `hcloud firewall replace-rules`). kubeconfig and
   `just ssh` work over the tailnet (`tls-server-name: kubernetes`, since the k3s
   cert has no SAN for the tailnet address). Terraform does not install
   tailscale, so a rebuilt node is reachable only through the Hetzner console
   until it joins.
2. **DNS-01 for prefect-server.** Create a scoped Cloudflare token (DNS edit,
   `waow.tech`), add a DNS-01 issuer, move the prefect-server certificate to it,
   and confirm a renewal. While there, find the ACME solver pod that has been
   stuck since July (`cm-acme-http-solver-*`) and which certificate it belongs to.
3. **migrate consumers** (below), each verified against the live server.
4. **flip.** Point the `prefect-server.waow.tech` A record at the tailnet
   address, and add a Traefik `ipAllowList` on that host for the tailnet range
   plus the pod CIDR (needs `externalTrafficPolicy: Local` on Traefik's service
   so source addresses survive). Keeping the hostname keeps every hardcoded URL
   and Discord link working, and spindle CI guests, which cannot use MagicDNS,
   can still resolve it.
5. **verify** from off-tailnet (refused) and from the Mac, the phone, heavypad,
   and an in-cluster pod (served), then run a real deployment end to end.

## consumers

| consumer | runs on | today | change |
|---|---|---|---|
| home-pool worker, prefect-mcp-selfhosted, phi-inference, dagster | heavypad (tailnet) | public URL via public DNS | nothing beyond the DNS flip; confirm heavypad resolves to the tailnet address afterward |
| laptop worker, `just prefect`, operator CLI | Mac (tailnet) | public URL | nothing; Tailscale must be connected |
| prefect-ios app, Safari | iPhone (tailnet) | `/api`, `/push`, events socket | Tailscale on-demand; Siri intents and notification actions need the VPN up |
| iPhone Shortcuts presence | iPhone, background | `POST /api/presence` | unreliable without VPN; move to `hub.waow.tech/api/presence` behind Cloudflare with a path bypass, or keep an exception |
| hub pod | in-cluster | secret `presence-ingress/prefect-api-url` | set to `http://prefect-server:4200/api` (prefect-push already uses service DNS) |
| prefect-push | in-cluster | inbound `/push` from the app | follows the app onto the tailnet |
| spindle CI deploys (my-prefect-server `deploy.yml`, stream `admission.yml`) | heavypad microVMs | public URL | should route over heavypad's tailnet once DNS points there; verify one run |
| gardener-exe flow runs | exe.dev VMs (off tailnet) | worker env URL | tailscale is in the VM image; inject an ephemeral tagged auth key via `run_environment` |
| phi bot | Fly (off tailnet) | REST `/api/flow_runs/*` | tailscale on Fly with an ephemeral key, or an exception |
| hosted MCP `prefect-by-zzstoatzz.fastmcp.app` | SaaS (off tailnet) | headers carry the API URL | retire in favor of the heavypad MCP once phi is on the tailnet |
| evergreen probes | Cloudflare Worker | `/api/health`, grafana | drop or mark private the prefect and grafana entries |
| pub-search ingestion watchdog | GitHub Actions | optional Prefect check | drop the check or add the tailscale action |
| phi-sprites-spike pool | Fly Sprites | unused pool | retire |

Open questions: the contents of `presence-ingress/prefect-api-url`; whether
heavypad overrides DNS today (the docs say the worker polls "over Tailscale",
but the hostname resolves to the public address); how phi on Fly reaches
heavypad today.

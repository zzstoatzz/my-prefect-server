# COSTS.md

> **Mandate.** This file tracks the infrastructure costs attributed to **my-prefect-server** and
> what we're doing to bring them down. It is the running record of what this project
> spends, why, and a changelog of changes that moved the number. If the spend here is
> unjustified, that's a signal to clean up — not to ignore it.

## current cost — fetch it live, never hardcode

Costs drift, so this file deliberately does **not** hardcode a dollar figure. Get the
current monthly cost for this repo from the daily snapshot (collected by
`my-prefect-server`, surfaced at https://hub.waow.tech):

```bash
curl -s https://hub.waow.tech/api/costs.json | jq '{
  as_of: .generatedAt,
  this_repo_monthly_usd: (
    [ .lineItems[] | select(.service as $s | ["prefect-server"] | index($s)) ]
    | (map(.amount) | add // 0) / 100
  ),
  lines: [ .lineItems[] | select(.service as $s | ["prefect-server"] | index($s))
           | {service, provider, usd: (.amount/100), estimated} ]
}'
```

Or open the costs panel at https://hub.waow.tech and group **by project**.

Services attributed to this repo: `prefect-server`. If that list is
wrong, fix the mapping in `my-prefect-server`
(`packages/mps/src/mps/costs/projects.py`) rather than editing numbers here.

## known-wrong state and where attribution is going

- the `COSTS.md` snippet in each repo fetches a live figure from
  `https://hub.waow.tech/api/costs.json`. **as of 2026-08-13 most of those
  figures are wrong**: 7 of 10 repos report `$0` because the snippets
  exact-match a `service` name (`leaflet-search-backend`) while fly and
  cloudflare line items gained component suffixes
  (`leaflet-search-backend:compute`) on 2026-06-17. prefix-match to get the
  real number until the generator is fixed.
- attribution currently infers project ownership from resource-name substrings
  in `packages/mps/src/mps/costs/projects.py`. that is the wrong model and is
  being replaced: **each project should declare what it owns**. resource names
  outlive renames (`pub-search`'s fly apps are still `leaflet-search-*`) and
  collide (bare `relay` vs plyr's `relay-api`), so inference silently
  mis-attributes and nothing fails loudly. the collector's job is to reconcile
  declarations and report conflicts + orphans; ~20 live services are claimed
  by no repo today. the protocol sketch is [docs/cost-declaration.md](docs/cost-declaration.md).

## how we might bring this down
- biggest line is usually `prefect-server` — check its utilization and right-size before anything else.
- Hetzner is billed at **EU list price** (already off the US premium); next lever is right-sizing the server type to real utilization.

## changelog
- **2026-06-17** — split Cloudflare cost collection into R2 bucket line items plus explicit fixed resource lines, so `plyr.fm` receives its R2/domain share instead of Cloudflare landing under `shared`.
- **2026-06-16** — moved `prefect-server` cpx31 Ashburn (US) → cpx32 Falkenstein (EU): **$73.49 → $41.99 (−$31/mo)**.
- **2026-06-17** — initial cost notice; 1 service(s) attributed here. Run the command above for the live figure.

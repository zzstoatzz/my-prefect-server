# costs

> **Mandate.** This file tracks the infrastructure costs attributed to **my-prefect-server** and
> what we're doing to bring them down. It is the running record of what this project
> spends, why, and a changelog of changes that moved the number. If the spend here is
> unjustified, that's a signal to clean up — not to ignore it.

## Home migration accounting (October 27)

The original Hetzner server, 150 GB volume and both primary IPs are deleted.
They have no continuing inventory charge. That does not erase usage already
accrued on the October invoice. The retirement receipt records their removed
monthly list-price run-rate; provider invoices remain authoritative for final
charges, taxes, credits, and proration.

Ephemeral builders use a durable ledger, not inventory snapshots. The worker
retains creation/deletion observations and the project's currency, server and
IPv4 rates under `~/hcloud-builder/state/billing/`. A minute timer copies these
non-secret receipts immutably to the existing backup bucket under
`billing/hetzner-builders/`. The independent Cloudflare expiry reaper writes its
own receipts directly to the same bucket and reconciles asynchronous deletions.
Worker and reaper receipts deduplicate by provider server ID. Billing telemetry
failure must not prevent expiry cleanup. Active builders and their IPv4s are
excluded from the fixed-inventory collector to prevent double charging.

The [commissioning backfill](audits/2026-10-27-builder-billing-backfill.json)
covers all twelve provider create/delete pairs visible in this project's action
history, beginning 06:46:58 UTC on October 27. Their conservative list-price
estimate is $0.8879, including hourly rounding and IPv4. Historical size/location
are inferred from the commissioning source (cpx32/fsn1), not recovered from a
provider invoice. New receipts capture those properties before deletion.

OpenRouter's `/api/v1/key` supplies actual usage for the configured key's current
UTC month, including DeepSeek and any other models using that key. It excludes
other keys, credit-purchase fees and upstream BYOK charges. The cost block is a
derived consumer of canonical `openrouter.api_key`; no new issuer key was made.
TypeSafe account-wide reconciliation is explicitly deferred at Nate's request;
Coral's existing Jev meter remains a partial application estimate.

OpenRouter and builder entries are explicitly tagged **month-to-date** and retain
their observed spend and window start. Existing infrastructure rows remain
monthly list-price/run-rate estimates. The UI labels mixed totals as recorded
costs, not a monthly forecast. Commissioning spend is included once and never
extrapolated into a recurring charge. The ledger retains individual evidence for
reconciliation; these estimates are not invoices.

Remaining explicit limits: Fly stopped rootfs sizes, paid IP/network charges,
provider discounts/taxes/credits, R2 operations beyond available meters, home
power/network, and uninstrumented third-party usage are not a complete invoice.
Do not describe a successful snapshot as account-wide billing completeness.

Bandwidth is on the existing [HeavyPad host dashboard](https://prefect-metrics.waow.tech/d/heavypad-host/heavypad-host).
The watchdog writes numeric textfile metrics for the existing node exporter;
there is no separate listener. See the [measurement limits and Xfinity comparison](../deploy/bandwidth-watchdog/README.md#grafana-bandwidth-comparison).

## current cost — fetch it live, never hardcode

Costs drift, so this file deliberately does **not** hardcode a dollar figure. Get the
current monthly cost for this repo from the daily snapshot (collected by
`my-prefect-server`, surfaced at https://hub.waow.tech):

```bash
curl -s https://hub.waow.tech/api/costs.json | jq '{
  as_of: .generatedAt,
  this_repo_monthly_usd: (
    [ .lineItems[] | select(.service | startswith("prefect-server")) ]
    | (map(.amount) | add // 0) / 100
  ),
  lines: [ .lineItems[] | select(.service | startswith("prefect-server"))
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
  by no repo today. the protocol sketch is [docs/cost-declaration.md](cost-declaration.md).

## how we might bring this down
- biggest line is usually `prefect-server` — check its utilization and right-size before anything else.
- Hetzner is billed at **EU list price** (already off the US premium); next lever is right-sizing the server type to real utilization.

## changelog
- **2026-10-22** — cost tracker: fly compute is priced by each machine's measured uptime over the trailing 7 days instead of extrapolating whatever was started at collection time (cc1c989); `pear` (pear-definitelyarealcompany), `follower-weight` and `porxie` are attributed instead of unattributed (cc1c989, 3732d51); a turso connector adds the org's scaler plan ($29/mo list) under `shared` (cfda92c). Method and caveats are in evergreen's COSTS.md accuracy table.
- **2026-06-17** — split Cloudflare cost collection into R2 bucket line items plus explicit fixed resource lines, so `plyr.fm` receives its R2/domain share instead of Cloudflare landing under `shared`.
- **2026-06-16** — moved `prefect-server` cpx31 Ashburn (US) → cpx32 Falkenstein (EU): **$73.49 → $41.99 (−$31/mo)**.
- **2026-06-17** — initial cost notice; 1 service(s) attributed here. Run the command above for the live figure.

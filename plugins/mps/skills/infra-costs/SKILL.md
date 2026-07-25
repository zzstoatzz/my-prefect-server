---
name: infra-costs
description: Read or debug the daily infra cost snapshot across Fly, Hetzner, Cloudflare, and Neon. Use when asked whether a teardown reduced spend, why a provider total moved, how a resource is attributed to a project, or when adding a cost connector. Also covers Fly volume and snapshot cleanup.
---

# infra costs

The `costs` flow (cron `0 8 * * *`, daily 08:00 UTC) collects provider billing,
writes an `io.zzstoatzz.cost.snapshot` PDS record at `rkey=YYYY-MM-DD` (upserted,
so re-runs are idempotent), and surfaces it at hub.waow.tech.

## reading a snapshot

Records are public — no auth needed:

```bash
DID=$(curl -s "https://api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle=zzstoatzz.io" | jq -r .did)
curl -s "https://pds.zzstoatzz.io/xrpc/com.atproto.repo.listRecords?repo=$DID&collection=io.zzstoatzz.cost.snapshot&limit=30" \
  | jq -r '.records | sort_by(.uri) | .[] | [(.uri|split("/")|last), (.value.total/100)] | @tsv'
```

Record fields are `total`, `currency`, `byProvider`, `byProject`, `lineItems`,
`periodStart`, `periodEnd`, `generatedAt`. There is **no** `items` field —
`jq '.value.items'` silently yields nothing and looks like zero cost.

## comparing spend across dates — read this first

Snapshots are a measurement, and the measurement has changed. **Diff
`lineItems` by service, not `byProvider`/`byProject` totals**, or a coverage
change will masquerade as a spend change.

Known discontinuity: on **2026-07-20** the Fly connector changed shape
(`7a212e8`). Before, one item per app (~26 items) with machines and volumes
bundled and snapshots not billed at all. After, a
`app:compute` / `app:volumes` / `app:snapshots` split (~55 items) with snapshot
storage newly counted — roughly $11/mo of previously invisible cost appeared.
Any before/after window spanning that date understates real savings.

Worked example: deleting the typeahead builder removed a $15/mo volume, yet the
`typeahead` project total moved only ~$2.40, because newly-counted snapshots
absorbed the difference.

## project attribution

There is no provider-side project tag. Attribution is by **resource name
pattern**, longest match wins, in `packages/mps/src/mps/costs/projects.py`
(`_RESOURCE_PATTERNS`). So `relay.waow` → `relays` while `relay-api-staging` →
`plyr.fm`. Adding a resource whose name doesn't match a pattern lands it in
`misc` or `unattributed` — add the pattern and a `project_for` assertion in
`tests/test_costs.py`.

## fail-closed is the invariant

A connector that raises is collected into `failed_providers`, and the flow then
**refuses to publish** rather than shipping a snapshot with a silent hole
(`flows/costs.py`). Never let a connector swallow an error and return partial
results — an omitted resource must never be counted as $0.

Caveat, currently true: the Hetzner Robot branch catches `httpx.HTTPError` and
only prints, so a Robot outage under-reports instead of failing closed. That is
inconsistent with the invariant above and is not yet fixed.

Hetzner specifics: Cloud API tokens are **project-scoped** (no account-wide
token), so the flow loads a `hetzner-tokens` Secret block of `{label: token}`
and merges all projects, deduping by server id. Robot/dedicated servers use a
separate Webservice API and need a `hetzner-robot` block carrying credentials
plus a `monthly_usd` registry — required because auction inventory does not
expose its winning price.

## Fly cleanup

`fly volumes destroy <id>` is **broken without `-a`** — its app-lookup GraphQL
query fails with `Field 'node' doesn't exist on type 'Queries'`. This is
Fly-side, not a stale client. Always pass the app:

```bash
fly volumes destroy -y -a <app> <vol_id> [<vol_id>...]
```

`fly volumes snapshots list` hits the same broken query with no `-a` escape.
Use the Machines REST API instead:

```bash
TOK=$(fly auth token)
curl -s -H "Authorization: Bearer $TOK" \
  "https://api.machines.dev/v1/apps/<app>/volumes/<vol_id>/snapshots" | jq .
curl -s -H "Authorization: Bearer $TOK" \
  "https://api.machines.dev/v1/apps/<app>/volumes/<vol_id>" \
  | jq '{size_gb,snapshot_retention,auto_backup_enabled,blocks,blocks_free}'
```

Destroyed volumes linger in a pending-destroy state and still appear via the
REST API after `fly volumes list` stops showing them. The connector ignores
them (`3efa153`), so they are not double-counted.

### snapshots are not cruft

Every volume defaults to `auto_backup_enabled: true`,
`snapshot_retention: 5` — a daily snapshot at ~00:02 UTC with a rolling 5-day
window. The count is a bounded steady state, not accumulation, and snapshots are
never read except on restore. Deleting them individually is pointless; they
regenerate. The only real lever is retention:

```bash
fly volumes update <vol_id> --snapshot-retention <days> -a <app>
```

Retention changes apply to **future** aging-out, so cost falls over the
following days rather than immediately.

Judge retention by whether the data is authoritative or derived. `typeahead-search`
holds a **derived** index — `typeahead-index` rebuilds it every 3 days and
publishes to R2, explicitly "no ingress, no SLA" — so it is set to 2 days.
`pds-zzstoatzz-io` is authoritative PDS data and stays at 5. Do not lower
retention account-wide.

## related

Running the flow by hand is [[adhoc-runs]]; the Secret blocks it needs are
injected per [[deployments]].

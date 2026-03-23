# hub

action item dashboard at [hub.waow.tech](https://hub.waow.tech). aggregates issues and PRs from github and [tangled.org](https://tangled.org), scores them by importance, and generates a daily briefing with an LLM.

## data sources

two ingestion flows run hourly at :00, writing to separate DuckDB tables:

**gh-notifications** (`flows/gh_notifications.py`) — fetches github notifications (issues + PRs) and open items authored by `zzstoatzz` via the search API. each issue is cached by repo+number for 24h. persists to `raw_github_issues`.

**tangled-items** (`flows/tangled_items.py`) — fetches issues, PRs, and comments from the tangled.org PDS (`pds.zzstoatzz.io`) via AT Protocol's `com.atproto.repo.listRecords`. no auth needed — records are public. targets repos: zat, zlay, plyr.fm, at-me, pollz, typeahead. persists to `raw_tangled_items`.

## pipeline

```
github API ──► gh-notifications ──► raw_github_issues ──┐
               (hourly :00)                              │
                                                         ▼
                                                  ┌─── enrich (dbt) ───┐
                                                  │    (hourly :05)    │
                                                  └────────────────────┘
                                                         │
tangled PDS ──► tangled-items ───► raw_tangled_items ────┘
               (hourly :00)                              │
                                                         ▼
                                                  hub_action_items
                                                    (mart, top 200)
                                                         │
                                          ┌──────────────┼──────────────┐
                                          ▼              ▼              ▼
                                       curate      /api/cards.json   +page.svelte
                                    (hourly :10)                     (SSR loader)
                                          │
                                          ▼
                                    briefing.json ──► /api/briefing.json
```

## flows

| deployment | schedule | what it does |
|---|---|---|
| `diagnostics` | `*/5 * * * *` | prints system info — canary for worker health |
| `gh-notifications` | `0 * * * *` | github notifications + authored open issues/PRs → `raw_github_issues` |
| `tangled-items` | `0 * * * *` | tangled.org issues/PRs/comments → `raw_tangled_items` |
| `enrich` | `5 * * * *` | dbt build: staging → enrichment → mart. concurrency limit 1. runs under python 3.13 (dbt-core compat) |
| `curate` | `10 * * * *` | loads top 200 scored items, sends to claude haiku 4.5 via pydantic-ai, writes `briefing.json` |
| `cleanup` | `0 2 * * 0` | deletes old terminal flow runs (completed, failed, cancelled, crashed) older than 30 days |

all flows run in the `kubernetes-pool` work pool. code is pulled at runtime via `git clone` from tangled.sh (github fallback). deps install via `uv run --with 'my-prefect-server @ git+...'`. deployments are registered by CI on every push to main.

## dbt layer

project lives in `analytics/`. DuckDB database at `/var/lib/prefect-analytics/analytics.duckdb`.

| model | type | description |
|---|---|---|
| `stg_github_issues` | view | dedup `raw_github_issues` by (repo, number), keep most recent fetch |
| `stg_tangled_items` | view | dedup `raw_tangled_items` by `at_uri`, exclude comments, keep most recent |
| `int_github_issues_scored` | table | scoring: recency (30-day decay) x engagement (log scale) x label multiplier (bug=1.5) x contributor weight |
| `int_tangled_items_scored` | table | scoring: recency (30-day decay) x 0.5 (no engagement data) x contributor weight |
| `hub_action_items` | mart | union of both scored tables, ordered by `importance_score` desc, limit 200 |

contributor weights come from the `known_contributors` seed (zzstoatzz + zzstoatzz.io at 2.0x).

## curation

the `curate` flow runs at :10 each hour after `enrich` refreshes the mart. it:

1. snapshots DuckDB to `/tmp` (bypass exclusive flock)
2. loads top 200 items from `hub_action_items`
3. sends them to claude haiku 4.5 with a system prompt that groups by actionability
4. writes a structured `Briefing` (headline, 2-5 themed sections with accent colors, icons, priority) to `briefing.json`

briefing model is defined in `packages/mps/src/mps/briefing.py`.

## frontend

SvelteKit app in `web/`. bun runtime, node adapter, port 3000.

### routes

| route | description |
|---|---|
| `/` | SSR page — loads stats, cards, and briefing in parallel |
| `/api/cards.json` | JSON array of scored action items from `hub_action_items` |
| `/api/briefing.json` | curated briefing object from `briefing.json` on disk |
| `/api/stats.json` | aggregate counts from `raw_github_issues` (tracked, open, with_reactions, repos) |

the frontend reads DuckDB through a snapshot copy (`/tmp/hub_analytics_snapshot.duckdb`) that refreshes when the source file's mtime changes. all queries are read-only.

### deployment

```bash
just web    # build + push + deploy
```

this runs: docker build (bun image) → push to `atcr.io/zzstoatzz.io/hub:latest` → apply k8s manifests → rolling restart. the hub pod mounts the analytics PVC at `/analytics` and reads `DUCKDB_PATH=/analytics/analytics.duckdb`.

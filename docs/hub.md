# hub

action item dashboard at [hub.waow.tech](https://hub.waow.tech). aggregates issues and PRs from github and [tangled.org](https://tangled.org), scores them by importance, and generates a daily briefing with an LLM.

## data sources

a single `ingest` flow runs hourly on cron and fetches both data sources concurrently, then writes to DuckDB sequentially (same process = no single-writer lock contention). downstream flows (transform, brief) are event-driven via deployment triggers — they only run when upstream completes.

**github** — fetches notifications (issues + PRs) and open items authored by `zzstoatzz` via the search API. each issue is cached by repo+number for 24h. persists to `raw_github_issues`.

**tangled.org** — fetches issues, PRs, and comments from the PDS (`pds.zzstoatzz.io`) via AT Protocol's `com.atproto.repo.listRecords`. no auth needed — records are public. targets repos: zat, zlay, plyr.fm, at-me, pollz, typeahead. persists to `raw_tangled_items`.

## pipeline

```
github API ──┐
             ├──► ingest ──► raw_github_issues ──┐
tangled PDS ─┘   (hourly)   raw_tangled_items ──┤
                                                 ▼
                                          transform (dbt)
                                          [on ingest ✓]
                                                 │
                                                 ▼
                                          hub_action_items
                                            (mart, top 200)
                                                 │
                                  ┌──────────────┼──────────────┐
                                  ▼              ▼              ▼
                                brief      /api/cards.json   +page.svelte
                          [on transform ✓]                   (SSR loader)
                                  │
                                  ▼
                            briefing.json ──► /api/briefing.json
```

## flows

| deployment | trigger | what it does |
|---|---|---|
| `diagnostics` | cron `*/5 * * * *` | prints system info — canary for worker health |
| `ingest` | cron `0 * * * *` | fetches github notifications + authored items and tangled.org items concurrently, persists both to DuckDB sequentially |
| `transform` | on `ingest` completion | dbt build: staging → scoring → mart. concurrency limit 1. runs under python 3.13 (dbt-core compat) |
| `brief` | on `transform` completion | loads top 200 scored items, sends to claude haiku 4.5 via pydantic-ai, writes `briefing.json`. cached by items content hash (skips LLM when data unchanged) |
| `cleanup` | cron `0 2 * * 0` | deletes old terminal flow runs (completed, failed, cancelled, crashed) older than 30 days |

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

the `brief` flow fires automatically when `transform` completes (via deployment trigger). it:

1. snapshots DuckDB to `/tmp` (bypass exclusive flock)
2. loads top 200 items from `hub_action_items`
3. checks cache — the `generate_briefing` task uses a `ByItemsContent` cache policy that hashes the items text + system prompt. if the data hasn't changed since the last run, the cached briefing is returned without calling the LLM (4h expiration)
4. on cache miss, sends items to claude haiku 4.5 with a system prompt that groups by actionability
5. writes a structured `Briefing` (headline, 4 themed sections with accent colors, icons, priority) to `briefing.json`

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

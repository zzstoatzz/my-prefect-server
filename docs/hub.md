# hub

action item dashboard and intelligence pipeline at [hub.waow.tech](https://hub.waow.tech). aggregates issues and PRs from github and [tangled.org](https://tangled.org), scores them by importance, generates a daily briefing with an LLM, and maintains phi's long-term memory.

## data sources

a single `ingest` flow runs hourly on cron and fetches all data sources concurrently, then writes to DuckDB sequentially (same process = no single-writer lock contention). downstream flows (transform, brief, compact) are event-driven via deployment triggers — they only run when upstream completes.

**github** — fetches notifications (issues + PRs) and open items authored by `zzstoatzz` via the search API. each issue is cached by repo+number for 24h. persists to `raw_github_issues`.

**tangled.org** — fetches issues, PRs, and comments from the PDS (`pds.zzstoatzz.io`) via AT Protocol's `com.atproto.repo.listRecords`. no auth needed — records are public. targets repos: zat, zlay, plyr.fm, at-me, pollz, typeahead. persists to `raw_tangled_items`.

**bluesky likes** — fetches nate's recent likes from the PDS via `app.bsky.feed.like` records, then batch-resolves post content via `app.bsky.feed.getPosts` (25 per call). persists to `raw_likes` and `raw_liked_posts`.

**phi memory** — reads phi's TurboPuffer namespaces (`phi-users-*`) to snapshot observations and interactions into DuckDB for dbt processing. persists to `raw_phi_observations` and `raw_phi_interactions`.

## pipeline

```
                        data sources
  ─────────────────────────────────────────────
  github API        ──┐
  tangled PDS       ──┤
  bluesky likes     ──┼──► ingest (hourly) ──► DuckDB
  phi memory (tpuf) ──┘
                                                  │
                                                  ▼
                                          transform (dbt)
                                          [on ingest ✓]
                                                  │
                        ┌─────────────────────────┼──────────┐
                        ▼                         ▼          ▼
                      brief                    compact    hub UI
                  [on transform ✓]         [on transform ✓]
                        │                         │
                        ▼                         ▼
                  briefing.json              TurboPuffer
                  /api/briefing              (phi-users-*)

                        standalone flows
  ─────────────────────────────────────────────
  morning (daily 8am CT)   ──► TurboPuffer + semble
  rebuild-atlas (every 6h) ──► Cloudflare Pages
```

## flows

| deployment | trigger | what it does |
|---|---|---|
| `diagnostics` | cron `*/5 * * * *` (inactive) | prints system info — canary for worker health |
| `ingest` | cron `0 * * * *` | fetches github, tangled.org, bluesky likes, and phi memory concurrently, resolves liked post content, persists all to DuckDB sequentially |
| `transform` | on `ingest` completion | dbt build: staging → enrichment → mart. concurrency limit 1. runs under python 3.13 (dbt-core compat) |
| `brief` | on `transform` completion | loads top 200 scored items, sends to claude haiku 4.5 via pydantic-ai, writes `briefing.json`. cached by items content hash (skips LLM when data unchanged) |
| `compact` | on `transform` completion | synthesizes per-user relationship summaries from phi's observations + interactions. extracts new observations from liked posts (LLM). writes summaries to TurboPuffer (`phi-users-*`). cached by observations content hash |
| `morning` | cron `0 13 * * *` (8am CT) | tag maintenance (dedup, merge, relationship discovery) + agentic semble curation (promotes observations to public cosmik cards). runs 1h before phi's daily reflection |
| `rebuild-atlas` | cron `0 */6 * * *` | rebuilds the leaflet-search 2D semantic map (UMAP + HDBSCAN on TurboPuffer embeddings), deploys to Cloudflare Pages |
| `cleanup` | cron `0 2 * * 0` | deletes old terminal flow runs (completed, failed, cancelled, crashed) older than 30 days |

all flows run in the `kubernetes-pool` work pool. code is pulled at runtime via `git clone` from tangled.sh (github fallback). deps install via `uv run --with 'my-prefect-server @ git+...'`. deployments are registered by CI on every push to main.

## dbt layer

project lives in `analytics/`. DuckDB database at `/var/lib/prefect-analytics/analytics.duckdb`.

| model | type | description |
|---|---|---|
| `stg_github_issues` | view | dedup `raw_github_issues` by (repo, number), keep most recent fetch |
| `stg_tangled_items` | view | dedup `raw_tangled_items` by `at_uri`, exclude comments, keep most recent |
| `stg_likes` | view | dedup `raw_likes` by `subject_uri` |
| `stg_liked_posts` | view | dedup `raw_liked_posts` by `subject_uri`, join with like timestamps |
| `stg_phi_observations` | view | dedup `raw_phi_observations` by observation id |
| `stg_phi_interactions` | view | dedup `raw_phi_interactions` by interaction id |
| `int_github_issues_scored` | table | scoring: recency (30-day decay) x engagement (log scale) x label multiplier (bug=1.5) x contributor weight |
| `int_tangled_items_scored` | table | scoring: recency (30-day decay) x 0.5 (no engagement data) x contributor weight |
| `int_phi_user_profiles` | table | aggregates per-user observation + interaction counts for compact flow |
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

## compact

the `compact` flow fires in parallel with `brief` when `transform` completes. it maintains phi's long-term memory in TurboPuffer.

**relationship summaries** — for each user phi has interacted with (from `int_phi_user_profiles`), loads their observations and recent interactions from DuckDB, resolves their bluesky profile, and sends to claude haiku to synthesize a dense relationship summary. writes to `phi-users-{handle}` namespaces as `kind="summary"` records. cached by observations content hash — skips the LLM when data unchanged.

**liked post observations** — loads recently resolved liked posts from DuckDB (`raw_liked_posts`), groups by author, queries TurboPuffer for existing knowledge about each author, searches pub-search for their publications, then extracts 1-3 atomic observations per author via LLM. uses ADD/UPDATE/NOOP reconciliation to avoid duplicating what phi already knows. writes to `phi-users-{author_handle}` namespaces as `kind="observation"` records.

the result: observations from likes are indistinguishable from observations phi creates during conversations — same schema, same namespaces, same reconciliation logic.

## morning

the `morning` flow runs daily at 8am CT (1h before phi's reflection). it has two halves:

**tag maintenance (phases 1-3)** — mechanical operations on TurboPuffer:
1. collect all tags across all `phi-users-*` namespaces
2. embed tags and identify near-duplicates via LLM (e.g., "atproto" / "at protocol" / "AT Protocol")
3. apply merges: rewrite tags in TurboPuffer, discover inter-tag relationships, store in `phi-tag-relationships` namespace

**agentic curation (phase 4)** — assembles phi's recent observations, episodic knowledge, existing cosmik cards, and tag relationships into a context bundle. sends to an LLM that decides what (if anything) deserves promotion to semble as a public cosmik card. executes the plan: creates `network.cosmik.card` records on phi's PDS, which semble's firehose subscriber auto-indexes for semantic search.

## atlas

the `rebuild-atlas` flow runs every 6h. it clones leaflet-search, runs the build-atlas script (PCA → UMAP → HDBSCAN on TurboPuffer document embeddings), produces `atlas.json`, and deploys the static site to Cloudflare Pages via wrangler.

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

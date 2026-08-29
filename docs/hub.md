# hub

action item dashboard and intelligence pipeline at [hub.waow.tech](https://hub.waow.tech). aggregates issues and PRs from github and [tangled.org](https://tangled.org), scores them by importance, generates a daily briefing with an LLM, and maintains phi's long-term memory.

## data sources

a single `ingest` flow runs hourly on cron and fetches all data sources concurrently, then writes to DuckDB sequentially. downstream flows (classify-emails, transform, brief, phi-memory-synthesis) are event-driven via deployment triggers — they only run when upstream completes.

DuckDB allows one read-write process per file, and flows on independent schedules (e.g. `docket`'s archive vs `transform`'s dbt build) can collide on it. every RW open of `analytics.duckdb` therefore holds the `analytics-duckdb-writer` global concurrency limit (limit=1, server-side) via `mps.lock.analytics_write_slot`, so writers queue instead of dying on the file lock. read paths avoid the lock entirely by snapshotting the file to `/tmp` first.

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
                                          classify-emails [on ingest ✓]
                                                  │
                                                  ▼
                                          transform (dbt)
                                          [on classify-emails ✓]
                                                  │
                        ┌─────────────────────────┼──────────┐
                        ▼                         ▼          ▼
                      brief                    phi-memory-synthesis    hub UI
                  [on transform ✓]         [on transform ✓]
                        │                         │
                        ▼                         ▼
                  briefing.json              TurboPuffer
                  /api/briefing              (phi-users-*)

                        phi identity flows
  ─────────────────────────────────────────────
  phi-tag-maintenance (daily 8am CT)   ──► TurboPuffer
          │
          ▼
  curate                   ──► Semble API
  phi-atlas (daily 8am CT) ──► PDS atlas blob
          │
          ▼
  docket                   ──► PDS docket blob

                        publication flows
  ─────────────────────────────────────────────
  leaflet-atlas (every 6h)       ──► Cloudflare Pages
  pub-search-snapshot (every 2h) ──► R2
  typeahead-index (every 3d)     ──► R2
  bisk-snapshot (every 10m)      ──► R2
  pds-records (ad hoc)           ──► PDS record maintenance
```

## flows

| deployment | trigger | what it does |
|---|---|---|
| `diagnostics` | cron `*/5 * * * *` (inactive) | prints system info — canary for worker health |
| `ingest` | cron `0 * * * *` | fetches github, tangled.org, email, bluesky likes, and phi memory concurrently, resolves liked post content, persists all to DuckDB sequentially |
| `classify-emails` | on `ingest` completion | LLM-categorizes new inbox emails (personal/work/notification/promotional) in cached 25-email batches so scoring can down-weight promotional mail |
| `transform` | on `classify-emails` completion | dbt build: staging → enrichment → mart. concurrency limit 1. runs under python 3.13 (dbt-core compat) |
| `brief` | on `transform` completion | loads top 200 scored items, sends to claude haiku 4.5 via pydantic-ai, writes `briefing.json`. cached by items content hash (skips LLM when data unchanged) |
| `phi-memory-synthesis` | on `transform` completion | synthesizes per-user relationship summaries from phi's observations + interactions. extracts new observations from liked posts (LLM). writes summaries to TurboPuffer (`phi-users-*`). cached by observations content hash |
| `phi-tag-maintenance` | cron `0 13 * * *` (8am CT) | tag maintenance (dedup, merge, relationship discovery) in TurboPuffer. runs 1h before phi's daily reflection |
| `curate` | on `phi-tag-maintenance` completion | agentic Semble janitor. prunes phi's public knowledge graph (dupes, orphaned links, vague RELATED connections), files ungrouped cards into existing collections, trims collection descriptions, and reviews private observations. deliberately has no authoring tools — new cards/collections/connections are created live by the bot, not from review (that loop once collapsed the library into one-topic self-synthesis) |
| `phi-atlas` | cron `0 13 * * *` (8am CT) | builds phi's daily private/public semantic atlas from TurboPuffer + PDS records, writes `io.zzstoatzz.phi.atlas/self` |
| `docket` | on `phi-atlas` completion | synthesizes promotion-pressure candidates from the atlas and writes `io.zzstoatzz.phi.docket/self` |
| `bufo-traffic` | cron `7 * * * *` | rolls find-bufo.com requests from logfire (zig HTTP spans + the retired rust backend's access logs, routes normalized in SQL) into one `io.zzstoatzz.bufo.traffic` record per UTC day on the operator PDS; the bufo-bot stats page draws its traffic charts from those public records |
| `leaflet-atlas` | cron `0 */6 * * *` | rebuilds the pub-search 2D semantic map (UMAP + HDBSCAN on TurboPuffer embeddings), deploys to Cloudflare Pages |
| `pds-records` | ad hoc, no schedule | operator flow for listing/creating/updating/deleting PDS records; default deployment is dry-run list mode |
| `costs` | cron `0 8 * * *` (daily 08:00 UTC) | collects provider billing (neon/cloudflare/hetzner/fly connectors), writes an `io.zzstoatzz.cost.snapshot` PDS record, surfaced at hub.waow.tech |
| `typeahead-index` | cron `0 9 */3 * *` (every 3rd day) | builds the typeahead prefix-index snapshot on the home box (zig batch job), publishes it to R2, rewrites `latest.json`. no ingress, no SLA |
| `typeahead-plc-identity` | cron `0 5 * * 1` (weekly Mon) | bulk-resolves typeahead's DID → (handle, pds) backlog from the PLC log (~28GB of bundles; heavy batch, no SLA) |
| `typeahead-enrich-backfill` | ad hoc, no schedule | paced profile backfill for typeahead's enrichment queue |
| `pub-search-snapshot` | cron `40 */2 * * *` | builds the pub-search FTS snapshot from the Turso document corpus and publishes it to R2 |
| `bisk-snapshot` | cron `*/10 * * * *` | computes the authoritative bisk.social standings and publishes a static snapshot to R2 for the edge site to adopt |
| `phi-curation` / `phi-editorial` / `phi-character-retro` / `phi-chicken-precheck` / `phi-chicken-scout` | crons (weekly Mon 03 / daily 15 / monthly 1st 17 / daily 04 / daily 18, UTC) | thin `phi-trigger` deployments that kick named passes on the phi bot via its control API — the bot defines what runs, prefect owns the clock |
| `watch-fastmcp` | cron `*/5 * * * *` | polls fastmcp's github notifications (conditional requests) and emits `github.<reason>` events onto the hub bus |
| `fastmcp-brief` | automations + cron `0 */4 * * *` floor | reads the recent event window off the bus and composes a brief when something merits surfacing |

all flows run on the `home-pool` process worker on the home box (heavypad); `kubernetes-pool` is retained as an unused fallback. code is pulled at runtime via `git clone` from tangled.sh (github fallback). deps install via `uv run --with 'my-prefect-server @ git+...'`. deployments are registered by CI on every push to main (`.tangled/workflows/deploy.yml`).

## dbt layer

project lives in `analytics/`. The full DuckDB database is
`/var/lib/prefect-analytics/analytics.duckdb`. `transform` also exports a slim
hub-only database at `/var/lib/prefect-analytics/hub.duckdb`, which is what the
SvelteKit hub mounts and reads.

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
| `int_phi_user_profiles` | table | aggregates per-user observation + interaction counts for phi-memory-synthesis flow |
| `hub_action_items` | mart | union of both scored tables, ordered by `importance_score` desc, limit 200 |
| `raw_llm_spend` | not a dbt model | materialized directly by `transform.py` from the append-only `llm-spend.jsonl` telemetry log (listed here for completeness) |

contributor weights come from the `known_contributors` seed (zzstoatzz + zzstoatzz.io at 2.0x).

## curation

the `brief` flow fires automatically when `transform` completes (via deployment trigger). it:

1. snapshots DuckDB to `/tmp` (bypass exclusive flock)
2. loads top 200 items from `hub_action_items`
3. checks cache — the `generate_briefing` task uses a `ByItemsContent` cache policy that hashes the items text + system prompt. if the data hasn't changed since the last run, the cached briefing is returned without calling the LLM (4h expiration)
4. on cache miss, sends items to claude haiku 4.5 with a system prompt that groups by actionability
5. writes a structured `Briefing` (headline, 4 themed sections with accent colors, icons, priority) to `briefing.json`

briefing model is defined in `packages/mps/src/mps/briefing.py`.

## phi-memory-synthesis

the `phi-memory-synthesis` flow fires in parallel with `brief` when `transform` completes. it maintains phi's long-term memory in TurboPuffer.

**relationship summaries** — for each user phi has interacted with (from `int_phi_user_profiles`), loads their observations and recent interactions from DuckDB, resolves their bluesky profile, and sends to claude haiku to synthesize a dense relationship summary. writes to `phi-users-{handle}` namespaces as `kind="summary"` records. cached by observations content hash — skips the LLM when data unchanged.

**liked post observations** — loads recently resolved liked posts from DuckDB (`raw_liked_posts`), groups by author, queries TurboPuffer for existing knowledge about each author, searches pub-search for their publications, then extracts 1-3 atomic observations per author via LLM. uses ADD/UPDATE/NOOP reconciliation to avoid duplicating what phi already knows. writes to `phi-users-{author_handle}` namespaces as `kind="observation"` records.

the result: observations from likes are indistinguishable from observations phi creates during conversations — same schema, same namespaces, same reconciliation logic.

## phi-tag-maintenance and phi identity

the `phi-tag-maintenance` flow runs daily at 8am CT (1h before phi's reflection). it owns
the mechanical TurboPuffer tag-graph cleanup:

1. collect all tags across all `phi-users-*` namespaces
2. embed tags and identify near-duplicates via LLM (e.g., "atproto" / "at protocol" / "AT Protocol")
3. apply merges: rewrite tags in TurboPuffer, discover inter-tag relationships, store in `phi-tag-relationships` namespace

`curate` is a separate event-triggered flow after `phi-tag-maintenance`. it reviews phi's Semble records and private observations with an agent and mutates Semble through `semble-api` instead of hand-rolled PDS writes — but its mutations are janitorial only: delete (dupes, orphaned links, vague RELATED connections), file cards into existing collections, and trim collection descriptions. it deliberately has no create tools. authoring (new cards, collections, connections) happens in the live bot at the moment something crosses phi's attention; a daily review loop that authored from its own library is how the graph once collapsed into one-topic self-synthesis.

`phi-atlas` is a separate daily flow that maps phi's private memory and public PDS records into `io.zzstoatzz.phi.atlas/self`. `docket` runs after `phi-atlas` and writes `io.zzstoatzz.phi.docket/self`, a small daily set of promotion-pressure candidates. those `io.zzstoatzz.phi.*` records stay as raw PDS writes because they are phi-owned records, not Semble graph mutations.

## atlas

the `leaflet-atlas` flow runs every 6h. it clones pub-search, runs the build-atlas script (PCA → UMAP → HDBSCAN on TurboPuffer document embeddings), produces `atlas.json`, and deploys the static site to Cloudflare Pages via wrangler.

## phi atlas and docket

`phi-atlas` runs daily alongside `phi-tag-maintenance`. It combines phi-owned PDS records
and TurboPuffer memory rows into a single 2D map, using cached embeddings where
possible and only embedding records that are missing vectors. It archives the
generated atlas JSON and uploads the current atlas to phi's PDS.
The original design contract lives in [phi-atlas.md](phi-atlas.md).

`docket` is event-triggered from `phi-atlas`. It reads the atlas, selects
high-pressure clusters, asks an LLM for candidate work items, and archives the
result in DuckDB while publishing the current docket back to PDS.

## spend tracking

LLM calls are recorded by shared helpers in `packages/mps/src/mps/spend.py`.
The helper writes an append-only JSONL file next to the analytics DB
(`llm-spend.jsonl` by default). `transform` materializes that log into
`raw_llm_spend` for analysis, while the hub reads the live JSONL file directly
for near-live totals.

The landing page shows phi-memory-synthesis 24h/7d spend, recent calls, top flows, the
models in use, and the prompt-cache hit rate for the window. Tiny nonzero costs
are displayed with enough decimal places to avoid misleading `$0.00` rows for
cheap embedding calls.

The call count usually dwarfs the dollar figure because every LLM flow uses
prompt caching (cache reads bill at 0.1× input price). The panel's "why so
cheap?" link and [docs/prompt-caching.md](./prompt-caching.md) explain the
strategy and where each flow configures it.

## frontend

SvelteKit app in `web/`. bun runtime, node adapter, port 3000.

### routes

| route | description |
|---|---|
| `/` | SSR page — loads stats, cards, and briefing in parallel |
| `/api/cards.json` | JSON array of scored action items from `hub_action_items` |
| `/api/briefing.json` | curated briefing object from `briefing.json` on disk |
| `/api/stats.json` | aggregate counts from `raw_github_issues` (tracked, open, with_reactions, repos) |
| `/api/spend.json` | live LLM spend summary from `llm-spend.jsonl` |

the frontend reads DuckDB through a snapshot copy (`/tmp/hub_analytics_snapshot.duckdb`) that refreshes when the source file's mtime changes. all queries are read-only.

### deployment

```bash
just publish-web-remote    # build on the Hetzner node + deploy
```

this rsyncs the clean repo to the Hetzner node, builds
`atcr.io/zzstoatzz.io/hub:<git-sha>` there with buildah, imports it into k3s
containerd, applies the hub manifests, sets the deployment image, and waits for
rollout. The hub pod mounts the analytics hostPath at `/analytics`, reads
`DUCKDB_PATH=/analytics/hub.duckdb`, and reads live spend from
`/analytics/llm-spend.jsonl`.

`just web` remains as the older local Docker build/push path, but the normal
production path is `publish-web-remote`.

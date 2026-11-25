# hub

Action item dashboard and intelligence pipeline at [hub.waow.tech](https://hub.waow.tech).
It scores GitHub and Tangled issues, PRs and email, generates briefings after
successful transforms, and maintains Phi's long-term memory.

## data sources

a single `ingest` flow runs hourly on cron and fetches all data sources concurrently, then writes to DuckDB sequentially. downstream flows (classify-emails, transform, brief, phi-memory-synthesis) are event-driven via deployment triggers — they only run when upstream completes.

DuckDB allows one read-write process per file, and flows on independent schedules (e.g. `docket`'s archive vs `transform`'s dbt build) can collide on it. every RW open of `analytics.duckdb` therefore holds the `analytics-duckdb-writer` global concurrency limit (limit=1, server-side) via `mps.lock.analytics_write_slot`, so writers queue instead of dying on the file lock. read paths avoid the lock entirely by snapshotting the file to `/tmp` first.

**github** — fetches notifications (issues + PRs) and open items authored by `zzstoatzz` via the search API. each issue is cached by repo+number for 24h. persists to `raw_github_issues`.

**tangled.org** — fetches issues, PRs, and comments from the account's PDS (resolved from its DID document) via AT Protocol's `com.atproto.repo.listRecords`. no auth needed — records are public. targets repos: zat, zlay, plyr.fm, at-me, pollz, typeahead. persists to `raw_tangled_items`.

**bluesky likes** — fetches recent `app.bsky.feed.like` records, resolves recent
posts not already stored, and persists content and like timestamps in
`raw_liked_posts`.

**email** — reads Proton Mail Bridge into `raw_emails`. Classification writes
`raw_email_classifications`; dbt combines the two for email scoring.

**phi memory** — reads phi's TurboPuffer namespaces (`phi-users-*`) to snapshot observations and interactions into DuckDB for dbt processing. persists to `raw_phi_observations` and `raw_phi_interactions`.

The dbt execution layer is now [Dagster on HeavyPad](../deploy/dagster/README.md).
The Prefect `transform` flow submits the local Dagster job and waits for its
result, preserving the downstream event chain. Dagster records each dbt asset
and its observed schema/column lineage.

## pipeline

```
                        data sources
  ─────────────────────────────────────────────
  github API        ──┐
  tangled PDS       ──┤
  bluesky likes     ──┼──► ingest (hourly) ──► DuckDB
  email             ──┤
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
                  /api/briefing.json         (phi-users-*)

                        phi identity flows
  ─────────────────────────────────────────────
  phi-tag-maintenance (daily 13:00 UTC)   ──► TurboPuffer
  phi-atlas (daily 13:00 UTC) ──► PDS atlas blob
          │
          ▼
  docket                   ──► PDS docket blob

                        publication flows
  ─────────────────────────────────────────────
  pub-search-atlas (every 6h)    ──► Cloudflare Pages
  pub-search-snapshot (daily) ──► R2
  bisk-snapshot (every 10m)      ──► R2
```

## deployment ownership

[The home manifest](../prefect.heavypad.yaml) owns schedules, trigger chains,
pools and source pins. The trusted home registration service applies green main
revisions; CI itself does not register deployments. Git, wheel and image routes
have different delivery contracts; see [deployment validation](deployments-validation.md).

Ingest triggers classification, which triggers the Prefect transform handoff to
Dagster. Successful transform completion starts brief and memory synthesis;
phi-atlas completion starts docket. The FastMCP attention pipeline is described
[separately](fastmcp-attention.md). Read current cron expressions from the
manifest rather than maintaining another deployment inventory here.

## dbt layer

The project lives in [analytics/](../analytics/). Dagster runs dbt against
`/home/stoat/prefect-analytics/analytics.duckdb`, then its `hub_export` asset writes
the slim `hub.duckdb` that the SvelteKit hub reads. Its `llm_spend` asset
materializes the append-only spend log into `raw_llm_spend`.

Staging models deduplicate raw records; enrichment models score GitHub, Tangled
and email independently. `hub_action_items` combines all three, reduces source
scales above one without inflating low scores, then selects the top 200. The
[SQL models](../analytics/models/) define the scoring formulas; the
[analytics guide](analytics.md) records table ownership and consumers.

## curation

the `brief` flow fires automatically when `transform` completes (via deployment trigger). it:

1. snapshots DuckDB to `/tmp` (bypass exclusive flock)
2. loads top 200 items from `hub_action_items`
3. checks cache — the `generate_briefing` task uses a `ByItemsContent` cache policy that hashes the items text + system prompt. if the data hasn't changed since the last run, the cached briefing is returned without calling the LLM (4h expiration)
4. on cache miss, sends items to the configured briefing model with a system prompt that groups by actionability
5. writes a structured `Briefing` (headline, 4 themed sections with accent colors, icons, priority) to `briefing.json`

The model and cache policy are in `flows/brief.py`; the output schema is in
`packages/mps/src/mps/briefing.py`.

## phi-memory-synthesis

the `phi-memory-synthesis` flow fires in parallel with `brief` when `transform` completes. it maintains phi's long-term memory in TurboPuffer.

**relationship summaries** — for each user phi has interacted with (from `int_phi_user_profiles`), loads their observations and recent interactions from DuckDB, resolves their bluesky profile, and sends to claude haiku to synthesize a dense relationship summary. writes to `phi-users-{handle}` namespaces as `kind="summary"` records. cached by observations content hash — skips the LLM when data unchanged.

**liked post observations** — loads recently resolved liked posts from DuckDB (`raw_liked_posts`), groups by author, queries TurboPuffer for existing knowledge about each author, searches pub-search for their publications, then extracts 1-3 atomic observations per author via LLM. uses ADD/UPDATE/NOOP reconciliation to avoid duplicating what phi already knows. writes to `phi-users-{author_handle}` namespaces as `kind="observation"` records.

the result: observations from likes are indistinguishable from observations phi creates during conversations — same schema, same namespaces, same reconciliation logic.

## phi-tag-maintenance and phi identity

the `phi-tag-maintenance` flow runs daily at 13:00 UTC. it owns
the mechanical TurboPuffer tag-graph cleanup:

1. collect all tags across all `phi-users-*` namespaces
2. embed tags and identify near-duplicates via LLM (e.g., "atproto" / "at protocol" / "AT Protocol")
3. apply merges: rewrite tags in TurboPuffer, discover inter-tag relationships, store in `phi-tag-relationships` namespace

there is no background curator. a `curate` flow ran after `phi-tag-maintenance` until 2026-09-17: one Haiku agent tidied phi's Semble library and another hard-deleted rows from her per-person memory, both as phi and both outside her telemetry. the library half kept her shelves duplicative no matter how often she cleaned them; the memory half erased roughly 100 observations in its last two weeks. do not give a background flow write access to phi's library or delete access to her memory again. her own process reconciles observations on write and keeps retired versions readable.

`phi-atlas` is a separate daily flow that maps phi's private memory and public PDS records into `io.zzstoatzz.phi.atlas/self`. `docket` runs after `phi-atlas` and writes `io.zzstoatzz.phi.docket/self`, a small daily set of promotion-pressure candidates. those `io.zzstoatzz.phi.*` records stay as raw PDS writes because they are phi-owned records, not Semble graph mutations.

## atlas

`pub-search-atlas/pub-search-atlas` runs every six hours on HeavyPad. It builds the
map from turbopuffer embeddings, generates summaries, archives the assets, and
publishes them to Cloudflare Pages. The daily
`pub-search-snapshot/pub-search-snapshot` run builds one SQLite snapshot and
publishes it to R2 for Fly. It then atomically retains the same file on HeavyPad
at `~/prefect-analytics/pub-search-snapshot/replica.db`, where Atlas reads
publication metadata and extracts evidence locally. Each deployment owns one
schedule. Use the scheduled Atlas run for routine releases to avoid another
full embedding export.

## phi atlas and docket

`phi-atlas` runs daily alongside `phi-tag-maintenance`. It combines phi-owned PDS records
and TurboPuffer memory rows into a single 2D map, using cached embeddings where
possible and only embedding records that are missing vectors. It archives the
generated atlas JSON and uploads the current atlas to phi's PDS.
The original design contract lives in [the archived Phi atlas design](archive/phi-atlas.md).

`docket` is event-triggered from `phi-atlas`. It reads the atlas, selects
high-pressure clusters, asks an LLM for candidate work items, and archives the
result in DuckDB while publishing the current docket back to PDS.

## spend tracking

LLM calls are recorded by shared helpers in `packages/mps/src/mps/spend.py`.
The helper writes an append-only JSONL file next to the analytics DB
(`llm-spend.jsonl` by default). Dagster's `llm_spend` asset materializes that log
into `raw_llm_spend`, while the hub reads the live JSONL file directly
for near-live totals.

The landing page shows phi-memory-synthesis 24h/7d spend, recent calls, top flows, the
models in use, and the prompt-cache hit rate for the window. Tiny nonzero costs
are displayed with enough decimal places to avoid misleading `$0.00` rows for
cheap embedding calls.

Call volume alone does not explain spend. Inspect the selected model, token
usage, pricing basis and cache activity for the measured window; see
[prompt caching and spend](prompt-caching.md).

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

The hub runs on HeavyPad as `prefect-hub.service`, using the immutable image
and source revision in [services.toml](../deploy/prefect-edge/services.toml).
The container mounts `/home/stoat/prefect-analytics` read-only at `/analytics`
and reads the exported `hub.duckdb` and spend log locally. No Hetzner replica or
rsync hop remains. [Operations](operations.md) describes committed edge releases
and the production target wrappers.

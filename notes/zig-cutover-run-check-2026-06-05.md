# Zig cutover run check - 2026-06-05

## What ran

Manual production-worker runs through `just prefect deployment run ... --watch`:

| deployment | run | result | notes |
|---|---|---|---|
| `diagnostics/diagnostics` | `diagnostics-6a1c004a` | completed | Kubernetes worker submitted a job, cloned repo, ran Python 3.14 flow pod, emitted host/runtime info. |
| `ingest/ingest` | `ingest-a1963b17` | completed | Queried GitHub, tangled PDS, nate likes PDS/public post API, and TurboPuffer phi memory; wrote production DuckDB. |
| `transform/transform` | `transform-123df020` | completed | dbt compile OK; all staging/enrichment/mart models passed; exported fresh `hub.duckdb`. |
| `brief/brief` | `brief-69080906` | completed | Loaded 200 action items, called Anthropic, wrote fresh `briefing.json`. |
| `compact/compact` | `compact-71fee56e` | completed | Compacted 102 user profiles, wrote summaries to TurboPuffer, extracted 16 liked-post observations, wrote 7 added / 2 updated observations. |

## Data written / served

`ingest-a1963b17`:

- GitHub: resolved 93 issues/PRs; `raw_github_issues` total 3286.
- Tangled PDS: fetched and persisted 7 records; `raw_tangled_items` total 7.
- Likes PDS: fetched and persisted 15374 likes; `raw_likes` total 15375.
- Liked posts: resolved 30 posts; `raw_liked_posts` total 5142.
- Phi memory: persisted 840 observations and 109 interactions; totals 2460 observations / 362 interactions.

Hub after transform/brief:

- `/api/stats.json`: tracked 3286, open 1581, with_reactions 761, repos 59.
- `/api/cards.json`: 200 cards.
- `briefing.json` refreshed at 2026-06-05 04:54 UTC.

## Backlog / backup estimate

Declared schedules in `prefect.yaml`:

- `ingest`: hourly (`0 * * * *`).
- `morning`: daily 13:00 UTC.
- `phi-atlas`: daily 13:00 UTC.
- `rebuild-atlas`: every 6 hours.
- `transform`, `brief`, `compact`, `curate`, `docket`: event-triggered from upstream completions.

Before the manual catch-up, durable files showed:

- `hub.duckdb`: 2026-06-04 19:02 UTC.
- `briefing.json`: 2026-06-04 19:03 UTC.
- `analytics.duckdb`: 2026-06-04 20:01 UTC.

Manual catch-up ran around 2026-06-05 04:49-05:07 UTC, so the practical missed window was roughly 9 hours of hourly ingest/transform/brief/compact work. Most upstreams are backfillable because they are snapshot/list/upsert based:

- Tangled records and likes are PDS list reads.
- Phi memory is queried from TurboPuffer namespaces.
- Authored GitHub items are searched by open author query.

Main caveat: GitHub notifications are fetched with `only_unread=true`, so anything that became read during the gap may not be recoverable from the notifications endpoint. The authored-items search covers open items by `zzstoatzz`, but not every notification edge.

## Remaining Zig server issues found

- Deployment schedules are not persisted: after `just prefect deploy --all` with Prefect client 3.7.3, `deployment_schedule` row count remains `0`.
- Event automations are present but duplicated (`35` rows after repeated deploys) and did not create `transform` after `ingest` completed.
- Completed flow runs can leave Kubernetes Jobs/pods running. `ingest-a1963b17` and `compact-71fee56e` were `COMPLETED` in Prefect but their Kubernetes Jobs remained active until manually deleted.
- The server reports version 3.7.2 while the client is 3.7.3, producing a client warning.

## Cleanup done

- Deleted the stuck completed-run Jobs:
  - `ingest-a1963b17-6x7mt`
  - `compact-71fee56e-g4jmr`

Only normal Prefect pods remained after cleanup.

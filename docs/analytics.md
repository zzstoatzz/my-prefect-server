# analytics

`analytics.duckdb` on HeavyPad (`/home/stoat/prefect-analytics/`) is the
warehouse for personal activity data. Prefect flows write raw tables; Dagster
runs dbt over them hourly (`transform` hands off to it, see
[deploy/dagster](../deploy/dagster/README.md)). Part of the point is
dogfooding Prefect, Dagster and dbt together, so models should feed something
that is actually read.

## tables and who reads them

| table | written by | read by |
|---|---|---|
| `raw_github_issues` | `ingest` (notifications, authored, stored-open refresh) | hub, Grafana "github intelligence", `ingest`'s open-item refresh |
| `raw_tangled_items` | `ingest` | dbt `int_tangled_items_scored` |
| `raw_emails`, `raw_email_classifications` | `ingest`, `classify-emails` | `classify-emails`, `email-triage`, dbt `int_emails_scored` |
| `raw_email_triage` | `email-triage` | decision history (append per run) |
| `raw_liked_posts` | `ingest` (likes from the last 7 days, resolved once) | `phi-memory-synthesis`, hub discovery pool (phi reads it) |
| `raw_phi_observations`, `raw_phi_interactions` | `ingest` (copy of phi's TurboPuffer `phi-users-*`) | dbt `int_phi_user_profiles`, `phi-memory-synthesis` |
| `raw_phi_dockets` | `docket` | docket history (append per run) |
| `raw_llm_spend` | Dagster `llm_spend` (from `llm-spend.jsonl`) | ad hoc queries |
| `int_phi_user_profiles` | dbt | `phi-memory-synthesis`, which writes relationship summaries phi reads |
| `hub_action_items` (from the `int_*_scored` models) | dbt | `brief`, hub export |

The hub (`hub.waow.tech`, served from HeavyPad through the Cloudflare tunnel)
reads `hub.duckdb`, which Dagster's `hub_export` writes after dbt. Besides the
page, it serves `/api/agents/discovery-pool` (phi), `/api/costs.json` (cost
exports in other repos) and `/api/presence`.

Grafana reads a copy refreshed by `prefect-grafana-snapshot` when the file
changes; it never opens the live file.

## keeping it small

DuckDB keeps a deleted or replaced row version in the file until the file is
rewritten. Until 2026-11-01 every writer used `INSERT OR REPLACE` for every
fetched row, and the file reached 5.1 GiB for about 82 MiB of live data
(`raw_phi_observations` held 3.89M row slots for 3.6k rows).

- Writers go through `mps.db._upsert_changed`: new rows are inserted, changed
  rows replaced, unchanged rows left alone (`fetched_at` is not compared).
- `analytics-maintenance` rewrites the file nightly (09:37 UTC) under the
  writer lease with `COPY FROM DATABASE`, checks every table's row count, then
  swaps it in. It also drops relations listed in
  `mps.analytics.OBSOLETE_RELATIONS`; dbt never drops a model removed from
  the project.
- There is no row retention. Live data is small and its history is what
  models are built from; add a retention rule only for data with a reason to
  expire.
- Every DuckDB connection inside the Dagster unit sets `memory_limit` to
  `mps.analytics.DUCKDB_MEMORY_LIMIT`; see the
  [2026-11-01 incident](incidents/2026-11-01-transform-memory-throttle.md).

All read-write opens hold the `analytics-duckdb-writer` lease; readers copy
the file first.

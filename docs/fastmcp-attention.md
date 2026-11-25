# fastmcp attention

FastMCP issue and pull request triage runs in [Prefect Cloud](agentic-fastmcp.md).
The home-server GitHub watcher and Discord brief were retired in favor of the
Cloud maintainer digest. The retired watcher also carried personal GitHub
notifications such as mentions and review requests, which are available in
the GitHub inbox.

## analytics

The hourly warehouse supplies backlog, contributor-wait and stuck-PR evidence
for Phi's weekly review through `/api/agents/fastmcp-triage`; see
[analytics](analytics.md#fastmcp-triage). The old laptop Logfire dashboard contains
historical execution telemetry, not current worker health or pipeline coverage.

## backlog series

`fastmcp-backlog` (every 30 min, `heavypad-pool`) appends one row of open issue and
PR counts to `/home/stoat/prefect-analytics/fastmcp-backlog.jsonl`: totals, age
buckets (`lt_7d`, `d7_30`, `d30_90`, `gt_90d`), unlabeled, awaiting reporter
(`needs more info` / `needs MRE`), and for PRs draft and bot-authored. Rows with
`source: "backfill"` are daily reconstructions from created_at/closed_at and
carry only totals and age buckets. Row shape: `mps.github_backlog`.

- `just fastmcp-backlog 48` prints the last 48 rows.
- the `fastmcp-backlog` table artifact holds the latest counts, one version per
  run, through the Prefect API.
- `duckdb -c "select * from read_json('fastmcp-backlog.jsonl')"` on a copy for
  anything bigger.

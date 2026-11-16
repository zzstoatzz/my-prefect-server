# fastmcp attention

The home workflows observe FastMCP activity and surface it through briefs and
analytics. Coding automation lives in the separately owned
[Cloud workflows](agentic-fastmcp.md). The laptop triage worker and deployment
were removed October 22; old laptop run commands are not a supported path.

## current path

`watch-fastmcp` polls GitHub notifications and emits `github.<reason>` events.
`fastmcp-brief` reads recent events, removes already considered thread versions,
enriches current issue/PR state, and emits `hub.brief.ready` when something merits
a brief. `brief ready -> discord` delivers the notification.

At the November 15 audit, the four-hour brief schedule was active and both event
triggers (`fastmcp activity -> brief`, `fastmcp direct ask -> brief`) were disabled.
Their declarations remain disabled in `deploy/automations.yaml`. Do not describe
historical event-triggered runs as proof of the current trigger state.

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

## what "seen" means

`fastmcp_briefed_threads` (a Prefect Variable) maps thread_id → updated_at for
every thread the brief looked at, **including the ones the model discarded**.
It records what the pipeline has looked at. Nothing records what the operator
has seen.

The Variable is trimmed by `fit_briefed` to stay under Prefect's 5000-character
limit, oldest versions first. A trimmed thread that is still unread is news
again on the next run (`TestUnbriefed` pins both behaviors).

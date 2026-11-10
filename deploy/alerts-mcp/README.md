# alerts MCP

The alerts the Prefect server sends to Discord, readable by agents as
incidents, with a record of which agent is handling each one. Agents read
alerts here; nobody has to copy them out of Discord.

It runs on heavypad as the user unit `alerts-mcp.service` and listens on the
tailnet address only: `http://100.96.216.23:9012/mcp`. A request is served only
when `tailscale whois` attributes the connecting address to the login in
`ALERTS_TAILSCALE_LOGIN`. Agents on heavypad reach it at the same address.
There is no token to distribute.

## where alerts come from

Nothing writes alerts into this service. Every notification starts as a
`prefect.automation.triggered` event in the Prefect event log, and that event
carries the event that set it off. The server reads those for every automation
with a `send-notification` action, minus the names in `NOTIFICATIONS` in
`packages/mps/src/mps/alerts/model.py` (briefs, email digests, autofix and merge
updates). A new alerting automation in `deploy/automations.yaml` therefore
appears here with no further change.

Firings of one automation about one subject are one incident. The subject is
the deployment for flow-run failures and the triggering resource otherwise, so
`flow-run-failure/mcp-fleet-health` collects every failed run of that
deployment. History reaches as far back as the event log does, and at most 14
days.

## tools

- `list_alerts(status, hours)`: incidents that fired in the window. `active`
  (the default) is open plus claimed.
- `get_alert(key, hours, log_lines)`: firings, agent updates, and for a flow run
  the last log lines of the most recent failing run.
- `update_alert(key, agent, action, note, link)`: `claim`, `note`, `handoff` or
  `resolve`.

Status lives in the event log too. `update_alert` emits `alert.claimed`,
`alert.noted`, `alert.handed-off` or `alert.resolved` on the resource
`alert.<key>`; the service keeps no database. An incident reopens when it fires
again after being resolved. Two things resolve one without an agent: an event
whose name ends in `.recovered`, and for a flow-run failure a later completed
run of the same deployment (shown as `recovered_at`). Alerts for an event that
went missing have no such signal and stay open until an agent resolves them or
they leave the window. A second claim on a claimed incident is refused, and so is a
claim or handoff on a resolved one. The event log can take half a minute to
return an event it has accepted, so the service also counts the updates it
emitted in the last five minutes; that memory is per process and is lost on
restart. A handoff returns
it to open with the note as `needs`, which is how an agent without the right
credential passes it on.

The automation `alert update -> discord` posts claims, handoffs and resolutions
to the same Discord channel as the alerts. Notes are not posted.

## limits

- `agent` is whatever name the caller gives. Every caller is the same tailnet
  login, so this records intent and does not authenticate one agent to another.
- Alert text and logs are untrusted input to the agent reading them.
- The server reads flow-run logs with the worker's Prefect credential. Anything
  on the tailnet under the allowed login, and any process on heavypad, can read
  them through it.
- It changes nothing except alert status. Fixes go through the agent's own
  access.

## operating

```bash
just deploy-alerts-mcp
```

deploys the committed revision into `~/alerts-mcp/releases/<sha>` and restarts
the unit. Dependencies install from heavypad's package cache when it has them
(`uv sync --offline`), and the deploy says so when it has to download instead.
`pyproject.toml` pins the versions inbox-zero already uses there to make a
cache hit likely; the whole locked set is about 17 MB of wheels. The Prefect
credential comes from `~/.config/prod-worker/env`.

Tests: `uv run --extra inbox pytest tests/test_alerts_mcp.py`.

# Project inventory

Hub owns the operator-facing project inventory, resources, health results, and
cost views. `packages/mps/src/mps/projects.json` is the shared declaration; it ships
inside the mps package and is bundled into Hub. `/api/projects.json` publishes it.
The project page lives at `/projects/` in the existing Hub container.

The initial inventory includes Cloudflare Workers, Pages, D1, R2 and Fly apps
observed on 2026-09-09, plus Hetzner and Neon resources from the 2026-09-08 cost
snapshot. Resource ownership comes from project deployment configuration.
Unassigned resources remain visible. Wisp, Horizon, Durable Objects, home hardware,
and other provider accounts are not comprehensively surveyed. Presence and
suspended states are inventory facts, not availability or retirement decisions.

`fleet-health` reads this inventory and performs endpoint checks on heavypad,
retaining its existing Stream tail/seal/compaction checks and quarter-hour schedule.
It writes the `fleet-health-status` table artifact. Hub reads it with the existing
Prefect read-only credential and serves `/api/fleet.json`; opening or refreshing
Hub never launches another sweep. Reports older than 30 minutes, with missing
checks, or from a mismatched inventory return unavailable. Unchecked projects and
resources never inherit a green status from a passing homepage.

Endpoint tasks let the engine exhaust network retries before the join point
classifies them. A completed sweep with unhealthy findings returns
`Completed(name="Degraded")` and retains `fleet-health.unhealthy`. A sweep that
cannot complete fails. The separate protocol-level `mcp-fleet-health` still owns
MCP tool discovery; an HTTP initialize check is only endpoint availability.

Fleet findings also emit a warning to the existing zig-prefect-server Logfire
project. Alert `fleet health findings` (`9abe0387-4b1c-4cd7-a58a-d144669e00f8`)
selects the `prefect-flow-fleet-health` service and the `fleet health findings: `
message prefix, using a 30-minute window and five-minute evaluation. It reuses
phi's existing push channel, notifying on transitions into/out of matches.
Ordinary failed runs retain the existing server failure alert. No synthetic
production incidents are sent for testing.

`logfire-server-write-token` is a Prefect Secret block derived from the existing
k3s `logfire/token` secret. Rotation of that server telemetry credential must also
update this block before re-registering fleet-health. The token is injected at
deploy time and never loaded by flow code. Hub only receives `prefect-auth-ro`,
through `FLEET_PREFECT_API_AUTH_STRING`; its presence-ingress credentials and
existing endpoint are preserved.

Cost connectors use declared provider/resource keys rather than substring guesses.
A key matches the exact resource or a colon-delimited billing suffix. Hub applies
the same declarations when reading historical snapshots, whose old project labels
are known wrong. Both Hub cost displays consume that same API result. Unmatched
or ambiguous ownership stays unattributed; absent data is unknown, not zero.
No new cost collector or storage system is introduced.

Deploy the fleet and Hub from the same revision; schema/inventory mismatch is
intentionally visible during a cutover. Hub's container build now uses the repo
root as context to include the packaged inventory; `.dockerignore` limits context
to the web app and that JSON file. Preserve the current production presence route
when deploying from a worktree. The duplicate evergreen-health schedule remains
disabled and is removed from the deployment manifest. Evergreen's public address
can redirect here once its Tangled hosting configuration is repaired.

## Discord archive

Flow failures use the existing Prefect `flow-run failure -> discord` automation:
status, run name, and a direct link to the error and logs. The state message is
not pasted because the server's template engine does not implement truncation
filters and a long exception can exceed Discord's message limit. Logfire keeps
feeding phi through its existing raw-data channel; its duplicate Discord channel
is removed from the `flow run failed` alert after the Prefect template is applied.

Fleet findings carry a preformatted `summary`: at most five short lines, an
omitted-count indicator, and the Hub link. The full `unhealthy` array remains in
the event for machine consumers. Formatting is done before the event is emitted
because the server currently ignores Jinja filters, including `join`.

Apply just these templates with `just automations "--name 'flow-run failure -> discord' --name 'fleet unhealthy -> discord'"`.
Other projects' Logfire alerts still use Logfire's default Discord rendering;
this change does not introduce a new notification relay or change phi's triage.

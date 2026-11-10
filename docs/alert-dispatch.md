# Alert dispatch with Albedo

Prefect owns incident dispatch and approval. Albedo runs on HeavyPad with access scoped to the incident. A personal assistant such as Duet can surface blocked work from the cloud and help the operator decide how to proceed.

## From alert to diagnosis

1. A Prefect automation matches an eligible event type and source and starts a dispatch flow with the event ID. Discord receives human-facing updates.
2. The flow rejects explicitly marked tests and duplicate deliveries, checks for a shared outage, and gives a cheap model the alert and verified context. The model returns `investigate`, `skip`, or `needs_review` with a short reason. Only `investigate` starts Albedo. [Autofix reinstatement requirements](incidents/2026-11-06-home-internet-autofix-cascade.md#conditions-for-reinstating-autofix)
3. The pilot maps one fleet-health alert type to a pinned `my-prefect-server` checkout and grants investigation and local patch preparation. Prefect derives that scope from operator-owned configuration. For a failure across several deployments, start by diagnosing the shared dependency and identify the specific target before granting further access.
4. A reusable deploy skill can teach Albedo to read the repo instructions and `justfile`, identify the right recipe, and explain how to verify the rollout. Install it only in the dedicated incident sandbox during the pilot. Prefect governs which actions the run can take.

## When Albedo needs a decision

Albedo returns a structured request with its findings, the proposed target and action, and why it needs a scope change. Prefect records the request and pauses the flow. The request identifies the incident, flow run, and requested scope. When an authorized operator or separately authorized agent approves a specific scope, Prefect validates the decision against the pending request and resumes with that scope. The run can also be declined or expire.

The cloud assistant reads a concise pending-decision summary over MCP and brings it to the operator. It can help inspect the evidence and discuss the next action. Discord can show the same run link when Albedo starts, needs attention, or returns a result.

## Pilot and follow-up

Start with a marked test event, an unlabeled false alarm, and a real historical failure. Check that the marked test uses zero model calls, the triage model distinguishes the other cases, and duplicate delivery starts at most one Albedo run. Exercise a scope-change request with a controlled pause and resume before enabling live dispatch.

The self-hosted Prefect MCP currently offers read-only diagnostics. The existing alerts MCP is tailnet-only. The first cloud integration should expose just the pending decision and its status over an authenticated MCP path. A later MCP action could submit a typed decision for a specific paused incident, using explicit authority and the existing Prefect input mechanism. The operator can use Prefect directly in the meantime. [Alerts MCP](../deploy/alerts-mcp/README.md) · [Existing typed flow input](../flows/email_triage.py)

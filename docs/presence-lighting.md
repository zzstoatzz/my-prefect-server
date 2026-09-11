# Presence-driven lighting

The iPhone's Arrive and Leave Shortcuts automations send only `home` or `away`
and an observation timestamp. The home geofence stays on the phone. A dedicated
bearer token authorizes reports to `https://prefect-server.waow.tech/api/presence`;
the phone receives neither the PDS credential nor Prefect administration access.

## Current behavior

The endpoint queues `report-presence/phone-presence`. Under the strict
`home-presence-writer` concurrency limit (one slot), the flow stores the report
in the private ATProto record, verifies the stored value, and applies the preset:

- Home: recall Sahara in the living room; set all other lights to steady soft
  amber at 15% brightness.
- Away: turn every light off, including lights outside rooms.

The entire plan and expected state are validated before writing. Commands to
remaining lights continue if a bulb reports an error. Readback must verify every
light before the persistent marker advances; failures are visible in Prefect
and retry. A repeated state preserves manual lighting adjustments after a
successful application. An incomplete application retries the preset, so it can
reapply settings to lights that already succeeded. Older reports cannot actuate.

The marker is a small file on the single home worker, protected by the same
writer lock. It survives flow processes and worker restarts. Deleting it makes
the next report reapply its preset. This is deliberately a single-worker setup.
The last reported presence persists until a newer report; silence does not mean
away. Setting `apply_lighting=false` disables writes and returns the deterministic
policy decision. No LLM is needed for this fixed policy.

## Code

- `web/src/routes/api/presence/+server.ts`: authenticated JSON ingress, queuing
  only. HTTP 202 means queued, not that storage or lighting succeeded.
- `packages/mps/src/mps/presence.py`: private record validation and storage.
- `flows/presence_lighting.py`: serialized orchestration, retries, and marker.
- `packages/mps/src/mps/home_lighting.py`: preset planning and readback contract.
- `packages/mps/src/mps/lighting.py`: asynchronous smart-home MCP execution.

Lighting dependencies are in the `lighting` project extra, pinned to FastMCP
4.0.3 and the tested smart-home revision. Other workflows do not install them.

## Deployment

The running deployment uses the previously tested private source bundle on
heavypad. Local cleanup is not deployed automatically. Once a source remote is
available, `deploy/presence.yaml` replaces the temporary bundle/PYTHONPATH setup.
It registers only the phone-presence deployment; unrelated deployments are not
updated. Supply these variables to `just presence-deploy`:

| Variable | Value |
| --- | --- |
| `PRESENCE_SOURCE_URL` | HTTPS clone URL of this repository |
| `PRESENCE_SOURCE_REVISION` | Full published commit SHA |
| `PRESENCE_CREDENTIAL_FILE` | Worker path to the mode-0600 PDS password file |
| `PRESENCE_HUE_ENV_FILE` | Worker path to protected Hue configuration JSON |
| `PRESENCE_LIGHTING_STATE_FILE` | Persistent worker path for the state marker |

The flow source and installed package use the same revision. The worker needs
`uv` on PATH and the `home-presence-writer` global concurrency limit with limit 1.
Reuse the existing marker when replacing the deployment. The hub's deployment
ID must continue pointing to `report-presence/phone-presence`.

Hue configuration contains `HUE_BRIDGE_IP`, `HUE_BRIDGE_USERNAME`, and
`HUE_BRIDGE_CERTIFICATE` (a worker-local trusted certificate path). Credentials
are derived from the encrypted store, not stored in this repository. The
presence consumer's rotation gap is documented in the secret store README.

The hub uses the `presence-ingress` Kubernetes secret (`webhook-token`,
`prefect-api-url`, `prefect-api-auth`, `deployment-id`). An exact ingress rule
exposes `/api/presence` on the API hostname; other hub routes retain Cloudflare
Access. Missing endpoint configuration returns 503, missing authentication 401.
The JSON body accepts only `state` and `observedAt` with a timezone. The current
Prefect server accepted duplicate idempotency keys in testing, so correctness
relies on the flow's lock and marker rather than API deduplication.

## iPhone setup and verification

In Shortcuts, attach **Presence Home 2** (renamed “arrive home” on the phone) to
an Arrive personal automation and **Presence Away** (“leave home”) to Leave.
Select Run Immediately. Do not create Home accessory automations or share a
configured shortcut containing its bearer token.

The manual iPhone home report reached the private record and completed Prefect
run `824f929d-6c91-4b98-9ed6-9631940ec31f`. Home actuation completed in
`7b77ec40-16b0-4166-9cdf-95dada5da5d9`, with all 13 lights verified. A repeated
home report completed in `7a9b16b3-f291-4a4a-a145-f89d05c9157c` without advancing
the marker. Both phone automations were shown enabled. Actual geofence delivery
and departure actuation still await a real trip test.

## Private space and future notifications

The record owner is `nate.spaces-alpha.bsky.network`
(`did:plc:x5vcg5tj466g64de3jvvkzjg`). The private member-list space is
`at://did:plc:x5vcg5tj466g64de3jvvkzjg/space/io.zzstoatzz.home.space/home`, with
record `io.zzstoatzz.home.presence/self`. Anonymous reads were rejected.

The phone currently triggers Prefect directly. Editing the private record
independently does **not** trigger lighting. We have not implemented a space
notification subscriber.

Lore's TypeScript implementation in `pi-extensions/extensions/lore/` informed
the private-space design. Its `space.ts` handles delegation, DPoP credentials,
authority resolution, and cross-repo reads. Lore reads on session start and
explicit refresh; `announceWrite` is not a subscription. Lore's OAuth scope
covers its own collections and cannot silently authorize this presence space.
See <https://lore.waow.tech/llms.txt>.

ZDS's `docs/permissioned-data.md` and `src/atproto/space.zig` were inspected on
2026-09-05. `registerNotify` registers a resolvable service DID using a DPoP-bound
space credential; registrations expire after 24 hours in that implementation.
`notifyWrite` carries revision metadata, not record contents. A future receiver
must authenticate the authority, audience, expiry, and method, fetch current
state, renew subscriptions, and reconcile missed notifications. ZDS is a PDS
implementation, not the account hosting this record; compatibility must be
verified against the actual space host before adopting its notification path.

# Presence-driven lighting

The first slice is a manually updated home/away record in a private ATProto
Space, followed by a real Prefect run that proposes a lighting action. Physical
control comes after that path works. The generic `pi-agent` flow executes the
decision; private-space authentication and notification handling belong outside
the runner.

## Notification contract

Checked against ZDS's `docs/permissioned-data.md` and
`src/atproto/space.zig` on 2026-09-05:

- `com.atproto.space.registerNotify` takes `space` and `service`, requires a
  DPoP-bound space credential, and returns `expiresAt`. ZDS registrations last
  24 hours. The service is a resolvable DID, optionally with a service fragment;
  it is not an arbitrary webhook URL.
- The receiver implements `com.atproto.space.notifyWrite`. Its body contains
  `space`, `repo`, `rev`, and `hash`, not record contents. In ZDS's fanout path,
  the space authority signs the notice for the registered service audience.
  Authenticate that authority, audience, expiry, and method before enqueueing.
- Notifications are best effort. Renew before expiry and reconcile writer
  revisions through `listRepos` to recover missed notifications.
- Permissioned records do not appear on the public firehose. Whole-space
  enumeration requires a space credential; an authenticated resident can read
  their own writer repo through the resident access path.

## First end-to-end run

Reuse Lore's space and credential conventions.
Choose a dedicated presence record with home/away state and an observation
timestamp. Do not put a Pi prompt, tool configuration, or credentials in that
record.

On an authenticated notice, fetch the current presence record from its repo
host. Treat the notice as a wakeup: a delayed home notice must not override a
newer away record. Ignore unrelated record changes and deduplicate work by the
current presence record's CID, rather than every revision of the space.

Dispatch the generic flow with trusted instructions and an empty toolset for
the first run. Confirm the returned decision and record the source CID and run
ID. Once proven, install a lights-only extension on the home worker and add
serialized actuation with a fresh presence check before applying it. Preserve
explicit manual lighting choices while home; repeated notifications must not
reapply an arrival scene.

## Lore reference

Lore's source is in the sibling `pi-extensions` repository under
`extensions/lore/`, linked from <https://lore.waow.tech/llms.txt>.
`space.ts` implements delegation-token exchange, DPoP-bound credentials,
credential renewal, authority-host resolution, and cross-repo reads. `oauth.ts`
persists and refreshes user sessions. Its private registry demonstrates that
even the mapping to a space can remain private.

Lore does not register a notification receiver: it reads on session start and
on explicit refresh. Its `announceWrite` helper notifies a remote space host
about a writer revision; it is not a subscription for an agent.

The Lore permission set only grants its own space type and entry/registry
collections. A dedicated structured presence record needs its own grant;
silently reusing the existing Lore OAuth session will not authorize it.
Keep presence separate from Lore's intentionally unstructured breadcrumbs.

## Verified first slice

The owner is `nate.spaces-alpha.bsky.network`
(`did:plc:x5vcg5tj466g64de3jvvkzjg`). Its private member-list space is
`at://did:plc:x5vcg5tj466g64de3jvvkzjg/space/io.zzstoatzz.home.space/home`.
The `io.zzstoatzz.home.presence/self` record starts as `unknown`; we have not
asserted that the owner is home or away. Anonymous reads were rejected.

`flows/presence_lighting.py` reads the record on the home worker, then invokes
the generic Pi subflow with an empty toolset. Real run
`35a13373-29fb-437f-98ef-a9158250baf8` completed with `NO_CHANGE`. The existing
encrypted-store credential was staged temporarily with mode 0600 for this
verification and removed afterwards. Nothing was copied into flow parameters.
The temporary verification deployment is paused and is not an operational
deployment; its credential-file dependency is deliberately no longer present.

The notification receiver, service identity, subscription renewal, durable
worker authentication, deduplication, and physical actuation are still unwired.
Presence is the last reported state, retained until another report changes it.
The decision-only prototype treats future-dated observations as `NO_CHANGE`.
Signal health is separate: silence alone does not mean the user left home.
Do not enable lighting writes using this prototype.

## iPhone reporting contract

The agreed signal is home/away only. Shortcuts evaluates the home geofence on
Nate's phone; no coordinates are sent. Start with a manual shortcut, then attach
Arrive and Leave personal automations after the manual path is verified.

The prepared `POST /api/presence` endpoint accepts an
`application/x-www-form-urlencoded` body containing exactly `state` (`home` or
`away`) and `observedAt` (ISO 8601 with timezone). A dedicated bearer token
allows only reports through this route; neither the PDS password nor Prefect
admin credential belongs on the phone. The route returns 202 for queued work,
not for completed storage or a changed light.

The endpoint queues `report-presence`, which holds the strict
`home-presence-writer` concurrency slot while reading, updating, verifying, and
making the decision. The global limit must be created with limit 1 before use.
Older and equal timestamps do not overwrite the current record. The downstream
Pi call remains decision-only. API idempotency behavior still needs verification
against the deployed Prefect implementation.

The hub manifest references the optional `presence-ingress` Kubernetes secret;
with no configuration, this endpoint returns 503. Its keys are `webhook-token`,
`prefect-api-url`, `prefect-api-auth`, and `deployment-id`. These resources have
not been provisioned. The worker credential must be derived from the encrypted
store into a mode-0600 file referenced by `PRESENCE_CREDENTIAL_FILE`; the earlier
temporary file is not usable for deployment.

### Manual Shortcut setup (after deployment)

Create a shortcut with a choice of `home` or `away`, capture the current date,
format it as ISO 8601 with a timezone, and use **Get Contents of URL** to POST
to `https://hub.waow.tech/api/presence`. Set the `Authorization` header to
`Bearer <dedicated token>` and Request Body to Form. Add only the chosen `state`
and formatted `observedAt` fields. URL-encoded and multipart text forms are
accepted; files and additional fields are rejected. Display the response for
the first test. `queued: true` means accepted, not completed; verify the private
record and corresponding Prefect run separately.

Apple documents the POST/Form action at
<https://support.apple.com/en-au/guide/shortcuts/apd58d46713f/ios>.
After the manual path works, separate Arrive and Leave automations can supply
the state without asking. Geofence location stays in the phone's automation.
Do not share a configured shortcut containing the dedicated token.

# HANDOFF — delete this file when the work below lands

Written 2026-09-26 at the end of a deployment audit. Two jobs are open:

1. Replace the `watch-tangled-pulls` poll with a consumer that stays
   subscribed on heavypad.
2. Fix `phone-presence`, which fails every night.

A few smaller leftovers are listed at the end.

## state at handoff (verified 2026-09-26 ~08:15Z)

- 44 deployments on the server, down from 50. Deleted:
  - the musician-studio `continuous` and `studio-pilot` deployments, with their
    flows, runs and deployment concurrency limits
  - `typeahead-handle-repair`, `sprites-spike`, `pi-parameterized-worker-check`
    and `presence-lighting-verification`
- `typeahead-enrich-backfill` is re-enabled by `4b893e0` and pinned to that
  commit. It had been paused since 2026-09-11 by accident (`e683764`). Its
  first run is 20:00 UTC on 2026-09-26. Check that it completes, and that the
  typeahead ingester's `/health` lag stays low while it writes. The flow yields
  to `_ingestion_ready`.
- Evergreen's inventory no longer lists `studio-pilot` or `curate`
  (evergreen `0d9e93d`, Worker redeployed). The public `services.json` and the
  Worker's `/status` agree on 52 checks, all healthy.

## 1. watch-tangled-pulls → heavypad subscriber (decided: do it)

**Today.** `flows/watch_tangled_pulls.py` runs on `*/2 * * * *` (`prefect.yaml`,
around line 945). That's ~10k runs a month and 209 failures in the last 30 days.

Each run:

- drains `wss://stream.waow.tech/subscribe` from a saved `time_us` cursor,
  for up to 120 s or until it has been idle 8 s
- makes one `listRecords` call per reviewer (the operator and Phi) against
  their PDS, as a reconcile
- dedupes by comment URI in the Variable `autofix_handled_comments`
- calls `arun_deployment` on `autofix-revise` with
  `{"pull": ..., "comment_uri": ...}`

The cursor lives in the Variable `autofix_pull_comment_cursor`.

The filtering logic is already factored and tested, so reuse it:

- `relevant_comment()`, `drain()` and `reconcile()` in the flow file
- `mps.tangled`: `comment_subject`, `comment_text`, `parse_verdict`
- only the operator and Phi can trigger a revision
- a Phi comment triggers one only with a request-changes verdict
- gardener never triggers itself

**Target.**

- **Subscriber.** A small `mps` module, for example
  `python -m mps.pull_comment_bridge`, runs under systemd on heavypad. It holds
  the Stream websocket, reconnects with backoff, and persists the cursor.
  - For each relevant comment it emits a Prefect event, for example
    `tangled.pull.comment.created`, with the pull and comment URI in the
    payload or the related resources.
  - It dedupes as today.
  - Precedent to copy: `deploy/phi-inference/pi-sprites-worker.service`. It
    runs `mps.sprite_worker` from a venv under `/home/stoat/phi-spike-worker`,
    with `EnvironmentFile=/home/stoat/.config/prod-worker/env`,
    `Restart=on-failure`, `UMask=0077` and `MemoryMax`.
  - The stream is public, so no new secret should be needed. The Prefect API
    credentials already come from that env file.
- **Automation.** Add an automation to `deploy/automations.yaml` (applied with
  `just automations`) that maps the event to a `run-deployment` on
  `autofix-revise`, templating `pull` and `comment_uri` from the event.
  - Precedent: `fastmcp direct ask -> brief` in the same file.
  - Before relying on that precedent, verify that `run-deployment` parameter
    templating works on this server. It is the zig Prefect server; see the
    204 hang incident noted at the top of `deploy/automations.yaml`.
- **Reconcile.** Keep `watch-tangled-pulls` as the safety net only:
  - reconcile only, with no Stream drain, since the subscriber owns that
  - scheduled hourly instead of every 2 minutes
  - or split it into a new `reconcile-tangled-pulls` deployment and delete
    `watch-tangled-pulls`
- **Downstream consumers to keep working:**
  - `autofix-revise`, which runs on the `phi-sprites-spike` pool
  - `docs/autofix.md` (rung three of the ladder)
  - evergreen's inventory, which lists `watch-tangled-pulls` under
    "dev automation", so update `site/services.json` if the name changes
  - the `flow-run failure -> discord` automation
- **Health.** A systemd service that dies quietly is the failure mode to design
  against. Expose the last-event time or cursor age somewhere `fleet-health`
  or evergreen already checks. Don't add a new monitor.
- **Verify end to end.** Leave a real operator comment on a gardener pull and
  watch event → automation → `autofix-revise` start within seconds. Then
  confirm the hourly reconcile finds nothing new.

`watch-fastmcp` (`*/5`, ~4k runs a month, polls GitHub) is the next candidate
for the same treatment. It isn't decided yet. Self-hosted Prefect has no
webhook receiver, so this one needs a design choice first.

## 2. phone-presence fails every night (not yet diagnosed past this)

`phone-presence` is declared in `deploy/presence.yaml` and deployed with
`just ... --prefect-file deploy/presence.yaml`. It is not in `prefect.yaml`.
It pulls from `/home/stoat/presence`, and its entrypoint is
`flows/presence_lighting.py:report_presence`.

It is triggered per phone report, with no schedule. In the last 30 days: 7 of
32 runs failed.

- **Pattern:**
  - every failure is a night-time `{"state": "home"}` report with
    `apply_lighting: true`
  - daytime runs succeed
- **Error:** a `RuntimeError("Lighting failed: ...")` raised from
  `phue/bridge.py:153 set_light`, the PUT to a light on the Hue bridge.
  - The rich-formatted traceback truncates the bridge's actual error.
  - First get the real response. Log `repr` of the underlying exception, or
    reproduce the arrival-home lighting call directly. The `lights` skill
    covers the Hue setup.
  - Likely suspects:
    - a light that is unreachable or not on the network
    - an effect or scene the light doesn't support (the code checks
      `light.effects_v2`)
    - a night-only scene
- Add a regression test for whatever the cause turns out to be.

## smaller leftovers

- **Zero runs in 30 days.** These are manual or event-triggered, so zero may
  mean rare rather than dead: `pds-records`, `dep-bump`, `pi-pr`,
  `merge-approved`, `phi-pull-review`, `test-pull-patch`, and `autofix-revise`
  (one run, crashed 09-10). Nate hasn't said which to keep.
  - Don't delete `autofix-revise` while the autofix ladder exists.
  - Check evergreen `site/services.json` and `docs/autofix.md` before
    deleting any of them.
- **`scripts/deployments_inventory.py`** only reads `prefect.yaml`. So
  `docs/deployments.md` misses deployments registered from elsewhere:
  - `phone-presence` (`deploy/presence.yaml`)
  - `mcp-atlas` (self-deployed from the mcp-atlas repo)
  Make it list those, or at least name them, so the doc matches the server.
- **Heavypad** holds about 30 `atcr.io/zat.dev/stream:*` images at 245 MB each,
  roughly 7 GB. That's unrelated to Prefect but was noticed during the audit.

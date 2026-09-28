# HANDOFF — delete this file when the work below lands

Last updated 2026-09-28. The Stream subscriber (the original job 1) has landed;
phone-presence and the leftovers below are still open.

## landed 2026-09-28

- `mps.pull_comment_bridge` runs on heavypad (`deploy/pull-comment-bridge/`)
  and emits `autofix.revise-requested`; `autofix revise requested ->
  autofix-revise` starts the run. `watch-tangled-pulls` is an hourly reconcile
  that ends `Recovered` when the bridge missed something. fleet-health checks
  the bridge's `/health`. (`50a60bc`, `0795cc9`)
- Verified in prod with a synthetic event for a non-gardener pull: the
  automation started `autofix-revise` ~5 s later with both parameters rendered,
  and it ended `Skipped`. **Not yet done:** a real operator comment on a
  gardener pull (costs a Pi run and publishes a round, so it waits for Nate).
- `typeahead-enrich-backfill` retries `SQLITE_IOERR`/`SQLITE_BUSY` pipeline
  results (`a49b7a0`) after the 09-27 run died on one.
- The Variable `autofix_pull_comment_cursor` is no longer read by anything.

## phone-presence (probably a physical fault; code gap remains)

`phone-presence` is declared in `deploy/presence.yaml` and deployed with
`just ... --prefect-file deploy/presence.yaml`. It is not in `prefect.yaml`.
It pulls from `/home/stoat/presence`, and its entrypoint is
`flows/presence_lighting.py:report_presence`.

It is triggered per phone report, with no schedule. In the last 30 days: 7 of
32 runs failed.

- **Update 2026-09-28:** the night-only pattern did not hold. The last failure
  (09-26 19:09Z) was a *daytime* arrival; autofix diagnosed Zigbee
  "communication issues" on light `ea0d820f` (bulbs switched off at the wall).
  After the switches were restored, all four arrivals through 09-28 00:16Z
  succeeded, two after dark. What remains is the code gap: the Rich traceback
  still hides the bridge's actual error.

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
- **zig prefect-server bugs found 2026-09-28** (fix in the prefect-server repo):
  - reactive triggers ignore `for_each` (`generateBucketingKey` returns `"[]"`)
    and fire once per `within` window, so every `within: 60` automation drops
    a second matching event inside a minute — e.g. `flow-run failure ->
    autofix` diagnoses only the first of two near-simultaneous failures
  - the webserver segfaulted (exit 139) on 2026-09-26 07:33:56Z right after
    a flow delete hit `fk_flow_run__flow_id`
  - changing a deployment's schedule leaves its old auto-scheduled runs;
    python deletes them. 30 stale `*/2` watcher runs were deleted by hand
- **`fleet unhealthy -> discord`** matches only resource `fleet-health`, so
  `mcp-fleet-health` findings never reach Discord. It lives only on the server;
  move it into `deploy/automations.yaml` when fixing the match.
- **`watch-fastmcp`** (`*/5`, polls GitHub) is the next candidate for the
  subscriber treatment; self-hosted Prefect has no webhook receiver, so it needs
  a design choice first.
- **home-worker limits drifted from the repo.** `systemctl set-property` drop-ins
  in `/etc/systemd/system.control/prefect-home-worker.service.d/` set
  MemoryHigh=32G, MemoryMax=48G, TasksMax=4096; `deploy/home-worker/` still says
  12G / 18G / 800. Pick one and make the other match. (The worker's 27.8 GB
  MemoryCurrent on 09-28 was 24.5 GB page cache and 0.5 GB anon — not a leak.)
- **Heavypad** holds about 30 `atcr.io/zat.dev/stream:*` images at 245 MB each,
  roughly 7 GB. That's unrelated to Prefect but was noticed during the audit.

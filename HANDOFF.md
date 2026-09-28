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

## phone-presence (physical fault fixed; code gap remains)

`phone-presence` is declared in `deploy/presence.yaml` (not `prefect.yaml`),
pulls from `/home/stoat/presence`, entrypoint
`flows/presence_lighting.py:report_presence`.

The failures were not night-specific: the last one (09-26 19:09Z) was a daytime
arrival, diagnosed as Zigbee "communication issues" on light `ea0d820f`
because bulbs were switched off at the wall. Nate restored them on ~09-27 and
arrivals have succeeded since. What remains: `RuntimeError("Lighting failed:
...")` from `phue/bridge.py set_light` goes through a Rich traceback that
hides the bridge's actual error. Log the underlying exception's `repr` so the
next unreachable light names itself, with a regression test.

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
- **zig prefect-server (reproduced, fixed and deployed 2026-09-28 —
  prefect-server `bb340a7` + `c0992d4`, Helm rev 26; see its CHANGELOG):** bulk_delete/bulk_set_state
  ignored filters and acted on the newest `limit` rows; schedule changes left
  old auto-scheduled runs; `for_each` was ignored (default `within` 60 vs
  python's 0); deleting a flow with runs answered 404. Corrections to the
  earlier list: firing once per `within` window per bucket is *python's*
  design, not a bug — per-event firing needs `within: 0` or `for_each`. The
  09-26 webserver segfault did not reproduce (Debug, ReleaseFast, 127
  concurrent FK-failing deletes); cause unknown.
  - Config follow-up now that `for_each` works: `flow-run failure -> autofix`
    has `within: 600` and `flow-run failure -> discord` 60, so a burst of
    failures across deployments yields one diagnosis / one message. Adding
    `for_each: [prefect.resource.id]` would make them per run.
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

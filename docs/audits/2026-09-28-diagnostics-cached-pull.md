# Diagnostics cached source rollout

Deployed implementation commit `02223b519c112feac8f8bd0fe2561b81c58aec30` to the
existing `diagnostics` deployment on 2026-09-28. Source is on
`codex/cached-diagnostics-pull`, pushed to both Tangled and GitHub.

Only diagnostics' pull steps, matching package/source pin, and version changed.
The flow code, parameters, home-pool assignment, active hourly `37 * * * *`
schedule, and alerts were preserved. No worker modification or restart. No new
canary deployment was created; none existed to delete.

The existing failure automations to Discord and autofix and the sustained-burn
notification remained enabled. Verification did not trigger a failure.

## Real run

[diagnostics-20cb736b](https://prefect-server.waow.tech/runs/flow-run/20cb736b-bc70-4230-80c4-011b44a5028f)
was triggered through the authenticated Prefect CLI with `scan_cache_size=false`.
It finished `Completed`, and its worker process exited cleanly.

- Cached pull at 17:18:18 CDT: `cache_hit=true`, exact deployed SHA, 2,928,640-byte
  source archive, 0.030 seconds.
- Flow start 17:18:24.610; end 17:18:25.532 CDT.
- Its independent `/tmp/prefect-flow-run-k8si5vpw` workspace was removed on exit.
- Final package preflight on heavypad: cold fetch 1.496 seconds, warm pull 0.025
  seconds, both materialized the expected diagnostics source.

The cache was warmed by the package preflight before the real run. This verifies
an actual deployed cache hit; the cold path was verified outside orchestration
first to avoid avoidable failure alerts.

## Resource check

100 consecutive warm pulls on heavypad using the initial implementation's same
warm path (the final change only affects Git timeout cleanup):

| Observation | Result |
| --- | --- |
| File descriptors | 7 before, at 10, 50, and 100 pulls |
| RSS | 43,076 KiB before; 44,172 at 10; 44,176 at 50 and 100 |
| Child processes after sampled pulls | none |
| Median / maximum pull time | 20 / 22 milliseconds |
| Total CPU time | 2.25 seconds |
| Leftover run or cache staging directories | zero |

This is a bounded smoke test, not proof against every possible leak. All handles
use context managers; hashing streams from disk, Git archives to a file, cache
entries are bounded, and run files do not depend on retained cache entries.

Review identified a cold-path timeout issue: killing Git alone could leave its
transport subprocesses alive. The final version creates a process group and
kills and reaps it on timeout/interruption. A real Git/transport subprocess test
verifies that the delayed child cannot continue writing after timeout.

35 tests passed across the new pull-step tests and existing deployment validation
suite. Coverage includes offline cache hits, exact old pins, four concurrent
cold callers, run isolation, rollback after eviction, corruption recovery,
count/byte limits, unavailable remotes, invalid pins, and timeout cleanup.
Ruff, formatting, type checks on the module, static deployment validation, and
wheel build passed.

## Rollback and integration

Remove diagnostics' deployment-specific pull override to restore the shared
clone-and-checkout steps; restore its previous package/source pin
`@9cc4a85ef307a7abccf38bce2564fa484e80142b` if rolling the live deployment back.
The cache can remain; it is disposable and bounded.

The implementation branch has not been merged to main. The repository's main
CI registers all deployments, so a later deployment from unmodified main can
replace diagnostics' live override. Merge the reviewed change before relying on
it as a permanent rollout. Other deployments still use their existing pull steps.

## Git checkout compatibility follow-up

Replaced the archive with a self-contained Git bundle. Diagnostics now uses
97adcf00da811b4f1c779e604a74a5c3cbd9d972, pushed to both remotes. Each run owns
its Git objects, full ancestor history, and tags captured at cache creation.
Actual checkout preserves export-ignore files; origin points to the original
repository. No alternates or shared objects tie runs to cache retention.

36 tests passed, including git describe, history traversal, clean status, and
ancestor checkout after cache eviction. Ruff, module type checking, deployment
contracts, and wheel build passed. On heavypad, 100 warm pulls kept descriptors
at seven; mean 224 ms, maximum 251 ms, maximum RSS 42,564 KiB. Final-pin preflight:
cold 2.706 s, warm 0.223 s, bundle 12,445,115 bytes.

[diagnostics-9fcecc7d](https://prefect-server.waow.tech/runs/flow-run/9fcecc7d-0b7a-47b1-9d9b-aa4ef0a3f7f3)
completed at 17:30:20 CDT. The worker journal confirms the new pin, a cache hit,
and 0.196 s pull time. Schedules, parameters, pool, and pull configuration were
verified unchanged.

Caching remains explicitly enabled only for diagnostics. Ephemeral pods without
persistent storage gain no cross-run hits. This pinned Unix implementation is
not a universal replacement for every Prefect Git option; see the design scope.
The branch still needs integration into main.

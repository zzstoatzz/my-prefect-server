# Persistent home-worker source cache

All 36 Git-backed my-prefect-server deployments on home-pool use the pinned source
cache. The shared uncached pull remains available to other pools. Deployments that
already load wheel modules without pulling code keep that route. The separately
configured phone-presence deployment pulls another repository and is not included.

The helper caches a verified Git bundle per full MPS_PIN, preserving Git history,
tags, and independent checkouts. Warm pulls do not contact the source host. Each
repository retains at most eight commit entries and 512 MiB after successful pulls.
An oversized bundle serves its first run, then becomes an empty bypass marker;
subsequent runs clone normally while the marker is retained. Bypass entries share
LRU eviction. Removing the cache permits a new fill attempt.

Thirty-three deployments import the helper from their current pinned package.
pi-agent, autofix, and pi-pr intentionally retain older flow/package revisions.
Their pull steps invoke the same stdlib-only helper as a content-addressed host
script, preserving those revisions and their existing execution commands.

Run `just install-source-cache` before registering a manifest that references a new
helper digest. It installs an immutable copy beneath
`/home/stoat/.local/share/mps/source-cache/<sha256>/deployment_steps.py` on heavypad.
The deployment contract test verifies that the YAML digest matches the source.
The standalone invocation selects Python 3.13 through uv.
The helper runs with the deployment's existing MPS_PIN and shares the same cache.

Publish GitHub main before Tangled main (`just push`); Tangled CI validates and
registers deployments with MPS_PIN set to that release. Before manual registration,
run `MPS_PIN=@<release> just validate-deployments --release`. Preserve schedules,
parameters, triggers, and frozen pins when changing pull configuration.

Validate rollout with diagnostics and fleet-health, then inspect scheduled runs for
`mps.source_cache` records. A warm record should report `cache_hit: true`. Exercise
legacy source materialization directly in temporary workspaces without invoking
agent flow bodies. Rollback restores the prior deployment pull steps and tracking
package/source pin; cache contents are disposable and independent run checkouts
remain valid after pruning.

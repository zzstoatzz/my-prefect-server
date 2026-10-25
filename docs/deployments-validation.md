# deployment validation

`scripts/validate_deployments.py` owns the offline checks for this repository's
code-delivery contracts. It reports errors; it never changes deployment config,
imports flows, executes pull steps, or contacts Prefect.

```sh
just hooks                         # install pre-commit for this checkout
just validate-deployments           # quick static check
just check                         # static check plus the existing full suite
MPS_PIN="@$(git rev-parse HEAD)" just validate-deployments --release
```

The [pre-commit hook](https://pre-commit.com/#usage) checks staged content on
commit. CI invokes the same validator before the test suite and again, with
`--release`, immediately before registering deployments. Hooks can be skipped;
CI is the delivery gate. Direct `just prefect deploy` and API mutations bypass
that gate: run the release check first, using the same MPS_PIN for registration.
Installing the hook is required in each new clone; `just hooks` does that without
adding the entire application's dependencies to the hook environment.

## what it checks

- YAML anchors are resolved. Deployment-level `pull: []` replaces the global
  pull steps. A custom work-pool mapping does not inherit the environment of
  the similarly named anchor.
- A deployment that installs the mps wheel, with no pull steps or explicit
  image/worker path supplying files, must use an importable
  dotted entrypoint. The flow module must be declared in the mps wheel's Hatch
  force-includes, and the command or Sprite `local_packages` must include mps.
- Git package requirements use either the shared MPS_PIN template or a full
  explicit commit SHA. When pulling source, job env.MPS_PIN must match that
  requirement. Release mode requires a full SHA for the shared template.
- File entrypoints for the recognized Git route exist in this checkout. Basic
  config shape, entrypoint syntax, and duplicate deployment names are checked.

This catches the September 11 reconciliation regression: converting a wheel's
`flows.watch_tangled_pulls.watch_tangled_pulls` into
`flows/watch_tangled_pulls.py:watch_tangled_pulls` while keeping `pull: []`.
The inventory renderer accepts both forms; documentation formatting must never
choose the runtime's loading mode.

## what remains unverified

A passing check means the static contracts passed, not that a deployment will
run. Output explicitly lists unverified routes. An image-backed file entrypoint
with no pull steps can be valid; it is not rejected just because it uses `:`.
Unknown delivery mechanisms are reported for operator review, not certified.

Remote wheel contents and availability, old pinned Git revisions, package
installation, worker environment, arbitrary shell steps, network access,
credentials, import side effects and flow behavior require other checks. The
current checkout's wheel manifest does not prove an older wheel contains a
module. No Prefect schema compatibility or flow signature validation is claimed.

The next layers should inspect the exact deployed artifact, then load the flow
inside the intended worker environment using Prefect's loader with placeholder
fallback disabled. Keep the source checkout off the import path; never invoke a
flow body merely to test its entrypoint. These layers are not implemented yet.

## breadth review and follow-ups

The September 11 review covered flows and mps helpers, package build manifests,
the Sprite worker and home-worker units, automation registration, CI/just
recipes, inventory rendering, tests, and operator documentation.

| Boundary | Finding and disposition |
|---|---|
| Configuration to runtime | Five wheel routes retain dotted entrypoints. Regression checks now live in the validator tests rather than the documentation renderer tests. |
| Package to checkout | 24 custom job mappings lacked MPS_PIN propagation. Their source checkout now receives the same declared pin as the installed package. Existing explicit legacy pins remain unchanged. |
| Package ownership | The root distribution supplies mps helpers; the separate mps wheel additionally force-includes selected flow modules. A checkout import is insufficient evidence of wheel contents. |
| Execution | Home process jobs and Sprite jobs coexist. README and operations now describe both and point here for the delivery contracts. |
| Registration | CI still registers all deployments on a main push. Selecting changed deployments, comparing live state, preserving a rollback snapshot, and verifying completion need a separate rollout change. |
| Separate presence config | `deploy/presence.yaml` has its own source variables and `just presence-deploy` guard. This first validator gates the main manifest; it does not yet establish presence revision binding. |
| Automation writes | `scripts/apply_automations.py` is a separate mutation path. Static deployment validation does not check notification routing, recursive autofix triggers, or automation diffs. |
| Historical notes | `HANDOFF.local.md` and archived build logs describe past snapshots. Use current configuration and live state for operational decisions. |
| Runtime tests | Unit tests and successful registration do not certify artifact imports or successful worker execution. Artifact and worker preflight remain the highest-priority follow-ups. |

General entrypoint/storage preflight may belong in Prefect. This repository owns
its mps wheel layout, Heavypad paths, Sprite package declarations and revision
binding. Keep these policies here; an upstream validator should allow legitimate
image and worker-path deployments.

## Typeahead PDS publisher configuration — 2026-10-20

Historical configuration. The October 25 [automatic cloud refresh](automatic-typeahead-refresh.md)
supersedes this section's pool, schedule, channel and installation assumptions.

The configuration selects immutable Typeahead tag `pds-compact-31ec142-20261020`
(on main, with the qualified native source), `snapshot_format: compact`, and
`INDEX_PDS_MAX_ACTORS=10000`. It preserves the active cron `0 9 */3 * *`,
concurrency 1 / CANCEL_NEW, source loading, secret-block references, quiet-build
setting and bandwidth limit. No other deployment is changed by this source diff.
The worker selects Zig 0.17 from the checkout's `build.zig.zon`; that compiler is
installed on HeavyPad. The qualified recovery pair totals 23.3 GB; both its upload
and checksum readback use the existing 10M bandwidth limit.

The October 19 09:00 UTC scheduled compact publication completed at 11:25 UTC.
Protected pair readback, native admission, catch-up, same-watermark rollback and
empty-directory recovery passed. Those completed compact-rollout gates supersede
the pending status in the dated October 8–17 history below.

PDS qualification passed 5,055 full-corpus response comparisons, 5,175 comparisons
in each rollback direction with 60 mutations, real failed/retried/policy-change
replay and paired warm/cold measurements without a statistically supported global
latency regression. The PDS pair passed full R2 checksum readback on October 20.
Native Fly admission and the production cutover remain separately gated; this
configuration is not evidence of deployment or a future scheduled execution.
Record live registration and serving verification in the
[Typeahead rollout record](https://tangled.org/zzstoatzz.io/typeahead/blob/main/docs/plans/pds-homepage-rollout.md).

## Typeahead compact snapshots — local implementation, 2026-10-08

The typeahead flow accepts `snapshot_format="compact"`; its default remains
`"sqlite"`. Compact mode runs the existing Zig builder with local publication,
then invokes the typeahead repository's converter and verified paired publisher
in a separate retryable task. It retains the serving compact generation's
legacy source during local cleanup. Production still uses the legacy mode.

The typeahead release must include `scripts/snapshot_compaction` before opting
into this parameter. The existing channel and production-arming variables
remain authoritative. No schedules or deployment parameters have been changed.
Release performance, recovery, staged cutover, and scheduled-cycle gates must
pass before enabling compact publishing in the registered deployment.

For the real converter integration test, set `TYPEAHEAD_COMPACTION_SOURCE` to
the typeahead release checkout and run `tests/test_typeahead_compaction.py`.

The local rollout integration also preserves the historical quiet-build supervisor,
process-group cancellation, and longer build deadlines from `d776dbd`. Conversion
and publication use the same supervisor when `INDEX_QUIET=1`; the selected
typeahead checkout must contain `scripts/snapshot_compaction/quiet.py`. This
integration has not changed the registered deployment.

The selected typeahead revision must also build the native compactor shared
library. The flow supervises conversion once, passing `--unlimited-cpu` to
disable the converter CLI's nested supervisor; `INDEX_QUIET=1` still controls
the entire subprocess tree. The conversion format and exact-output checks are
unchanged.

The pending rollout configuration sets `INDEX_QUIET="0"` so useful build and
conversion work can use a full core. Quiet supervision remains available via
`INDEX_QUIET="1"`, but the previous 20% duty-cycle limit is not the rollout
default. This configuration has not yet been registered with the scheduler.

On 2026-10-09 the pending compact builder passed actual Linux conversion through
`publish_compact.fn` with `INDEX_QUIET=0` and `1`: native output equality, identical
retry, unchanged legacy source bytes, and rejection of an existing generation
with a mismatched watermark. This used a synthetic fixture and local channel,
not production publication or a complete scheduled build. See
[typeahead-builder-host-integration.json](typeahead-builder-host-integration.json).

On 2026-10-17, `codex/typeahead-compaction` incorporated current main through
`2e743f3`, preserving the deployed memory-synthesis, inbox, Redis, and bandwidth
work and current deployment loading configuration. All 16 typeahead
converter/pruning/retry tests passed (15 in the suite, then the explicitly enabled
converter case), including
the real native converter; static deployment validation reported zero errors.
These are local checks, not evidence of scheduled production publication. The
registered typeahead deployment still has empty parameters and does not set
`INDEX_QUIET`; its active cron is `0 9 */3 * *`. Deployment registration and
compact publication remain gated on the serving cutover.

On October 17 the compact serving cutover passed production freshness, alias, and
full smoke checks. Nate accepted the documented warm and legacy-control performance
exceptions after the matched reload gate passed. The source configuration now
selects `snapshot_format: compact` and immutable source tag
`compact-candidate-cc2d5a1` for `typeahead-index`; the cron, concurrency policy,
secret-block references, loading mode, and other deployments remain unchanged.
Registration and the next scheduled build still require verification. The serving
replacement remains pinned to its protected checkpoint until the root compact
publication is verified and admitted; the old backend remains available for rollback.

Registration was verified on October 17 at 19:44 UTC with package pin `e9c62cd`,
compact format, the qualified source tag, `INDEX_QUIET=0`, unchanged cron, and
concurrency 1 / CANCEL_NEW. The scheduler replaced the old queued runs; the next
run at October 19 09:00 UTC contains the compact parameters. The complete local
check passed 742 tests (3 skipped), lint, formatting, type checks, and web checks.
Tangled CI and one explicit retry failed before executing checks because the
container image pull returned a missing Content-Length error. Registration used
the repository CLI against the verified live server after local checks passed;
no CI success is claimed. The scheduled publication, admission, and cleanup
remain outstanding in the typeahead cutover runbook.

## CI execution environment

The deployment workflow uses Spindle's NixOS microVM engine with explicit Nix
packages. The check and registration steps retain the same validation gates.
On October 17, Docker on HeavyPad rejected the Nixery registry manifest because
its HEAD response omitted Content-Length. A direct pull reproduced the failure;
GET returned a valid manifest. The microVM engine provisions dependencies through
Nix and avoids this failing OCI registry path. Verification requires a completed
CI run through registration, not merely successful VM creation.

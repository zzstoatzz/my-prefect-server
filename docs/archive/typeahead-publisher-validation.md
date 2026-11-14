# Typeahead publisher validation, October 2026

Archived 2026-11-14 from `docs/deployments-validation.md` when the
`typeahead-index` deployment and its flow code were removed. The test file
and parameters named below no longer exist.

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

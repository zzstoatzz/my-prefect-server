# Automatic Typeahead snapshot refresh

Full publication, isolated installation, cached restart, guarded promotion,
rollback and prepared-candidate recovery passed October 25, 2026. The configured
cadence is 06:00 UTC on odd-numbered days, next October 27. Runs are at most
48 hours apart, with a 24-hour gap at some month boundaries. Live registration and queued parameters
are verified against the actual server. The public Worker uses the stable
transport route; no serving machine performs bulk installation.

## Data and deployment sequence

The existing cloud publisher builds the full corpus on local SSD using the
qualified `pds-compact-31ec142-20261020` builder. It uploads compact artifacts and
the matching legacy twin to `releases/search/<build-id>/`. Full checksum readback
precedes `control/builds/latest.json`. This publication never changes traffic.
`INDEX_ALLOW_PROD=0` preserves the historical root pointer for legacy recovery.

The rollout controller reads `control/search-routing.json` and selects the other
managed Fly app. Only `typeahead-startup-20261024` and `typeahead-search-refresh`
are managed slots. It checks the public serving identity before changing inactive
resources, creates a fresh 20 GiB volume, and starts the pinned installer image.
Unowned machines or changed routing stop preparation. The app's own slot pointer
is `control/slots/<app>/latest.json`.

Installation runs before the search process starts. It downloads and checks the
compact SQLite metadata, verifies the full prefix payload and writes its native
admission sidecar, then atomically records local activation. A failed install
never changes public routing. An unchanged admitted installation skips the full
scan on restart. During installation, the `downloading` phase includes Rclone's
post-copy checksum read; a preallocated file's length is not transfer progress.
Full validation can take minutes on the inactive disk while production continues.
The same-watermark legacy twin is retained remotely; the other
backend and its independent overlay provide immediate traffic rollback.

Before traffic moves, the controller checks global/exact/CJK/PDS searches, replica
lag below 600 seconds, latency within the transport budget, and a cached restart
at the final two shared CPUs / 1 GiB. It updates the route using `If-Match` on the
previous ETag. Public freshness and both aliases are observed after promotion.
Failures restore the previous route if it still belongs to this operation; an
ambiguous write outcome is read back before cleanup. Serving resources are never
deleted during failure cleanup.

The transport accepts only the two fixed origins and existing backend paths.
Routing metadata may be cached for one second; diagnostic route reads and inline
admin writes bypass that cache. Admin authentication still belongs to the backend.
The routing record changes at most once per successful generation; the recurring
job needs no Cloudflare administrative token.

## Recovery and retention

`resume_generation` accepts an already completed legacy or compact build under the
publisher's active/archive roots. It avoids rebuilding the indexer, exporting the source again, or repeating the
full corpus index build. The small compactor target is restored when needed. An incomplete compact conversion is discarded and
regenerated from its intact legacy source. Completed staging artifacts can be
republished into the durable managed namespace without copying local payloads.
The publication subprocess explicitly enables Rclone link following for this
wrapper; its source paths are checked against the publisher roots first.
All original artifact digests remain enforced by the publisher and installer.
If the requested generation is already selected, recovery repeats its semantic
and public acceptance checks instead of treating identity alone as success.

After acceptance, both current and previous generations and their twins are
retained in the managed R2 namespace. Older managed artifacts are pruned under a
route-identity guard. The previous Fly backend remains running until it is the
inactive slot for the next refresh. The separately retained original compact
backend is outside the controller's ownership and is not deleted by it.

Later October 25, the operator authorized retiring both superseded backends and
their 60 GiB disks after the smaller backend passed qualification. Both machines
and disks are now deleted; the two managed app identities remain, with the inactive
slot empty for the next refresh. The route has `previous: null`, so immediate
old-backend rollback is unavailable until another successful refresh. R2 artifacts
and pinned images remain for installation/replay recovery. See the
[retirement receipt](https://tangled.org/zzstoatzz.io/typeahead/blob/main/docs/ops/automatic-refresh-20261025.md#superseded-backend-retirement).

The Fly cost collector resolves `waiting_for_detach` entries through the per-volume
endpoint before pricing them: the list can retain a deleted test disk while the
canonical resource is already `pending_destroy`. The live check after retirement
found one such 20 GiB entry; excluding it removes a false $3/month charge.

`prefect.yaml` owns the schedule, format, immutable source/image pins, cloud paths,
reserves and app-scoped Secret-block references. The billing Fly credential remains
read-only. The two deployment credentials are backed by the encrypted secrets
store and scoped individually to the two apps. Rotate by minting a replacement
app-scoped token, writing it through SOPS stdin, syncing its Prefect block,
verifying an isolated deployment, then revoking the old token. Runs resolve the
blocks on startup; neither credential grants Cloudflare account access.

Cached restart uses explicit stop, confirmed stopped state, configuration update
with `skip_launch`, then start and confirmed started state before HTTP qualification.
This prevents the old process from answering readiness during an asynchronous update.
Ambiguous transport responses are reconciled against actual Machine state.

A failed candidate remains in the inactive slot for inspection and recovery. Run
`just prefect deployment run typeahead-index/typeahead-index -p resume_rollout=true`
to use the already-verified `control/builds/latest.json` publication. A matching
owned candidate is reused; otherwise only installation is repeated. No corpus build,
artifact upload or publication readback is repeated. Current identity, corpus floors,
slot pointer, image, readiness, restart, query and public gates still apply. The next
new generation cleans the inactive slot before preparing its replacement, so
failed preparation cannot accumulate an unbounded set of machines or disks.
The rollout task retries transient failures against the same verified publication
and matching prepared candidate, preserving the completed disk instead of starting
installation over. An unexpected change of publication rejects the retry.
Never persist `resume_rollout=true` into the scheduled deployment defaults.

The [Typeahead execution record](https://tangled.org/zzstoatzz.io/typeahead/blob/main/docs/ops/automatic-refresh-20261025.md)
contains full-cycle, rollback, retry and resource receipts. The [schedule receipt](https://tangled.org/zzstoatzz.io/typeahead/blob/main/docs/evals/startup-refresh-schedule-20261025.json)
records the active cron, next queued runs, cloud pool, source pin and normal parameters.

## Operator rollback

Run `just prefect deployment run typeahead-index/typeahead-index -p rollback=true`
to exchange the current/previous routing targets after checking the previous
backend. This does not rebuild, download, resize, or delete a backend. It checks
public freshness and direct query contracts afterward, restoring the prior route
if verification fails. The same command exchanges them again to return to the
newer backend. A still-serving previous snapshot older than the freshness limit
returns a Degraded state rather than claiming freshness was restored.

Completed data can be resumed with the per-run parameter
`resume_generation=/var/lib/typeahead-indexer/active/build-<id>`. This parameter
must not be saved into the scheduled deployment defaults.

Subprocesses run under a parent-liveness guard. Normal cancellation, timeout, and
parent death terminate the command's process group, including grandchildren. Disk
reserve checks wake at least once per second even if the command is silent.
Each command has a private temporary directory removed after its process group
stops. Recovery restores the native compactor from the persistent compiler cache after
a fresh checkout; it does not repeat an already completed corpus build.

Abandoned `.compact-release-*` conversion staging is reclaimed before a new build
and after verified archival. Cleanup only considers owned directories older than
ten minutes, skips symlinks, and preserves directories referenced by live process
files or mappings. Completed `build-*` generations are outside that cleanup.
The cloud-host test covered real open files, recent staging, completed data and
symlinks; 6.8 GiB left by the earlier cancelled conversion was removed.

The compatible server retained `anchor_date` on interval registration but queued
runs at registration time plus the interval. A fixed cron avoids refresh starvation
when deployments are frequently registered. Verify the queued dates, not just the
stored schedule object, after changing this configuration.

## Local build retention

Pruning runs before a new build's disk admission and after successful archival.
Keep the two newest logical generations, counting legacy/compact twins together,
plus the serving generation and its twin. A 503 freshness response can identify a
working but stale snapshot: valid `ok`, `probe_ok` and build identity still allow
safe retention. Unknown identity protects data; more than four retained directories
then fails the run instead of silently claiming successful cleanup. Symlinked
build directories are excluded. Existing local-SSD reserves and cancellation
cleanup remain enforced; failed payloads are preserved for recovery, and reserves
stop new work before it exhausts disk. See the [disk audit](audits/2026-10-25-worker-disk-cleanup.md).

## October 26 monitoring repair

The installer image is pinned to `sha256:37fa2b5ed4cf21c63a1e75fefd0f48d001c2cf3d0635f5ee16441c40cece6525`. It requires a Logfire write token on each managed app before installation and refreshes the native Turso client’s TLS validation clock between requests. Both managed apps have the existing project token staged from the canonical encrypted store. The Typeahead transport marks routed health probes so the sync alert follows the production slot. See the [Typeahead monitoring runbook](https://tangled.org/zzstoatzz.io/typeahead/blob/main/docs/plans/automatic-snapshot-refresh.md#search-telemetry). The schedule and builder source are unchanged.

## Native image updates on an existing snapshot

Rollout idempotence compares both the snapshot build ID and the active Machine's
immutable image digest. A changed image reuses the published artifacts, installs
on the inactive managed app's independent volume, qualifies and restarts that
candidate at final size, then conditionally switches routing. It does not rebuild
the corpus or restart the current serving Machine. Prepared-candidate recovery
also applies to this code-only rollout.

The October 26 sync-hardening image was
`sha256:34c5a61a794674e64802d5d633dd800a2adfab2c0d91f0d8ceb14e5c28a84da3`
(Typeahead source `06a714c`): response-aware sync watchdog feeding, HTTP-client
recovery and bounded failure backoff. Deployment verification is recorded in the
[Typeahead operations record](https://tangled.org/zzstoatzz.io/typeahead/blob/main/docs/ops/sync-hardening-release-20261026.md).
The existing builder source and schedule are unchanged.

Freshness admission also requires a known nonnegative lag below 600 seconds.
`sync_lag_s=-1` means unknown and cannot qualify a newly attached snapshot.
Availability-only checks of the existing serving target still allow stale data.
A real loopback HTTP regression covers unknown, stale and boundary lag values.


## October 27 overlay reader performance release — live

Image `sha256:d4c3111ee8ad058cb08f7ef02ffeb0a2826e1c328b61c12c29a6e9d3e93ca0ed`
(Typeahead `62266a3`) is live on the startup slot after guarded resume run
`bbcfa61e-2d95-4cf6-9d83-37c1dd088b67` completed October 27, 17:34 UTC.
It disables mapped reads of the mutable SQLite overlay on reader connections,
retaining the writer and attached-index settings, and includes guarded replay
writes and bounded snapshot metadata warming. Independent full-snapshot Fly
qualification matched all 5,040 candidate responses: cold p99 33.5 ms versus
4.35 seconds baseline; mixed search/sync p99 25.3 ms with 40 writer batches.
These are current-input measurements, not universal live latency guarantees.

Machine `8e3d79b70dd748` serves build `b1793109259-2b36-px05`; the refresh slot
Machine `801539a6551778`, build `b1793081565-0228-px05`, remains healthy for
rollback. Both remain at two shared CPUs / 1 GiB with independent disks.
Cached restart, semantic/PDS gates, conditional R2 promotion, live smoke and
ranking acceptance passed. Routed health and successful sync telemetry are
present for the new serving slot. The registered deployment pins package
`cd72bcf`; the existing builder, cloud pool, parameters and odd-day 06:00 UTC
schedule are unchanged. Public latency verification remains in progress with
the existing transport unchanged. See the
[Typeahead release evidence](https://tangled.org/zzstoatzz.io/typeahead/blob/main/docs/evals/perf-goal-20261027/release/overlay-pread-live.json).

README review: the orchestration architecture and usage are unchanged; the
image-specific verified state belongs in this operations document.

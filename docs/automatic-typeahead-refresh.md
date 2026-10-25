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

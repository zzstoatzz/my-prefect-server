# Automatic Typeahead snapshot refresh

Implementation and qualification in progress, October 25, 2026. The cloud index
schedule remains inactive until the full rollout and rollback qualification passes.
The public Worker now uses the stable transport URL; its R2 route still selects
the already qualified backend. No serving Fly machine was restarted for this work.

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
scan on restart. The same-watermark legacy twin is retained remotely; the other
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
publisher's active/archive roots. It avoids another compilation, source export,
and full corpus index build. An incomplete compact conversion is discarded and
regenerated from its intact legacy source. Completed staging artifacts can be
republished into the durable managed namespace without copying local payloads.
All original artifact digests remain enforced by the publisher and installer.

After acceptance, both current and previous generations and their twins are
retained in the managed R2 namespace. Older managed artifacts are pruned under a
route-identity guard. The previous Fly backend remains running until it is the
inactive slot for the next refresh. The separately retained original compact
backend is outside the controller's ownership and is not deleted by it.

`prefect.yaml` owns the schedule, format, immutable source/image pins, cloud paths,
reserves and app-scoped Secret-block references. The billing Fly credential remains
read-only. The two deployment credentials are backed by the encrypted secrets
store and scoped individually to the two apps. Rotate by minting a replacement
app-scoped token, writing it through SOPS stdin, syncing its Prefect block,
verifying an isolated deployment, then revoking the old token. Runs resolve the
blocks on startup; neither credential grants Cloudflare account access.

The final qualification record and registered schedule will be linked here after
the real full-cycle check passes. Current code is not proof that it ran.

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
Recovery restores the native compactor from the persistent compiler cache after
a fresh checkout; it does not repeat an already completed corpus build.

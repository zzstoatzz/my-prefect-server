# Tantivy slot operations

The controller supports explicit `tantivy_operation` requests to the existing
`typeahead-index` cloud flow. Typeahead serves only from Tantivy slots; the native
engine and its refresh, rollback and pruning paths were removed on 2026-11-02.
A preparation returns an immutable review plan; it never changes `current` or
`previous`. For manual preparation, the operator separately requests
promotion with the exact plan, current ETag and complete empirical release receipt.
Approved automatic refreshes promote only after the same qualification gates. Current outage/recovery evidence and experiment preflight live in Typeahead's
[October 31 retrospective](https://tangled.org/zzstoatzz.io/typeahead/blob/main/docs/retros/2026-10-31-search-expiry-outage.md).
The publisher runs at 06:00 and 18:00 UTC; exact immutable pins live in
`prefect.heavypad.yaml`. Check actual completed refreshes and the serving coverage
deadline before reserving a slot. The worker has a three-hour outer bound: observed
checkpoint preparation plus installation can exceed the former two-hour bound.
This is a failure cutoff, not a refresh-time guarantee. Each readiness phase has
a one-hour bound: the verified cold installation on a 1 GiB slot took about
38 minutes, too close to the previous 40-minute bound. The restart phase gets its
own readiness deadline; all phases remain inside the outer worker bound.

Only `typeahead-tantivy-a` and `typeahead-tantivy-b` are eligible. Every candidate
must carry owner `typeahead-snapshot-refresh`, role `tantivy-slot`, request/build
identity, pinned image digest, two shared CPUs, 1 GiB RAM and its own encrypted
20 GiB volume. Provider mount records may include name, encryption and size
metadata; identity still requires exactly one writable `/data` mount of the pinned
volume. Conflicting metadata, read-only options or extra mounts fail closed.
Serving machines and the installer's protected machines are never modified. An unrelated
machine or volume fences preparation. The existing checksum-pinned Typeahead
installer performs installation and runtime qualification; this controller does
not implement another installer or uploader.

An explicit request takes this shape:

```json
{"tantivy_operation":{"operation":"prepare","build_id":"b1793236000-candidate","image":"registry.fly.io/typeahead-qual-20261029@sha256:<approved digest>","request_id":"<32 lowercase hex>"}}
```

The published artifact manifest determines all source bindings. For
`sqlite-committed-prefix`, the primary source build, receipt, counts and watermark
describe the raw checkpoint. The fixed slot image (Typeahead `c476fc5`) also
requires a `rollback_native_anchor` and `legacy_build_id` in the checkpoint
artifact, and its guest fetches the anchor's exact manifest bytes from
`releases/search/<anchor build>/manifest.json` (the deployment's
`source_manifest_key`) before installing. That provenance is the only native
remnant: the controller carries the serving artifact's anchor into each new
build, never reads a native backend, and never deletes anything under
`releases/search/`. The controller writes immutable deployment metadata under
`control/tantivy-slots/<app>/<request>.json`, then passes only its key, SHA256 and
build ID to the guest. Guest credentials remain platform secrets. The durable
installation receipt is read by a fixed bounded Machines API exec command at
`/data/tantivy/installation.json`. Mutable runtime diagnostics are not treated as
immutable admission evidence. The controller independently reads provider shape,
process/instance identity, HTTP binary/strategy acknowledgements and source gates,
then verifies a stop/start using the cached installation before producing a plan.
While waiting, guard transport failures retry on a five-second cadence within the
original deadline. Route, lease and ownership violations still abort immediately;
a network timeout does not authorize any mutation or reset the wait budget.
Candidate urllib startup responses retry only HTTP 408/425/429/500/502/503/504,
plus transport timeouts. Other HTTP failures and TLS certificate verification
errors propagate immediately. Existing candidate startup readiness gates remain
required throughout the wait.

The R2 journal `control/tantivy-operations/<request>.json` is authoritative; local
files are only caches on the disposable worker disk. Every provider mutation has
an intent committed before submission. A lost create/start/stop response is
reconciled by exact identity and provider state, never by blindly repeating create.
A different publisher execution may resume only after the preceding execution is
confirmed terminal. An unknown provider result preserves resources and fences the
next mutation. Repeat the same request to reconcile it.

Preparation acquires a route-envelope mutation lease using conditional R2 writes.
The lease does not expire automatically. Every controlled routing update preserves
it and cannot target its app. A changed routing head aborts preparation. Direct R2 edits or provider mutations outside this
controlled protocol cannot be globally locked by this controller.

`{"tantivy_operation":{"operation":"cancel","request_id":"<same request>"}}`
stops only the exact owned inactive candidate and retains its volume and artifacts
for inspection or same-request recovery. It cannot cancel a serving machine or
silently resolve an unknown create/delete. Cleanup deletes only unrouted
generations under `releases/search-tantivy/`; the current, previous and pending
Tantivy generations are protected, and a routed generation without its R2
publication fences cleanup. Cleanup is fenced while a mutation lease exists;
retention identity is checked again before each deletion batch.

The default refuses to reuse a slot still referenced by `previous`. An explicit
`retire_previous_tantivy:true` additionally requires independently fresh/healthy
current Tantivy, complete non-test empirical release proof, exact old ownership,
the retained artifact manifest and its anchor manifest, and the bundle's presence
and size. Payload checksums are rechecked on actual restore rather than
downloading the entire corpus merely to retire a slot. The old routing metadata
remains until promotion. Retirement intent and exact old IDs are journaled before
stop/delete; only the same candidate can resume after failure.

Promotion is a separate explicit operation containing `plan_sha256`,
`expected_current_etag`, `release_receipt_key` and `release_receipt_sha256`.
Required proof gates are matched performance, full functional correctness, live
source/coverage, Worker/HTTP smoke, recovery and refresh lifecycle. Each must be
true with an evidence SHA256, bound to the exact binary, strategy, canonical rules,
image and final guest/volume budget. A test-only proof or preparation is rejected.
The existing full benchmark is a component result, not a substitute for these
release gates.

Before CAS, promotion rechecks actual provider/HTTP/source identity. It reads no
native origin. Unlike preparation, promotion does not depend on the old backend
being available. The route it writes is `{version, current, previous}`.
It can resume its own unchanged lease after an interrupted attempt; another
lease or changed route still fences it.
It releases the preparation lease, then uses the resulting ETag to install the
exact reviewed target. A competing lease or operator update invalidates that CAS.
Public verification checks engine/build/format/binary/strategy headers and the
canary, rather than build ID alone. Failed verification compensates only while the
same target and ETag remain current; operator changes win. A committed transaction
journal supports verification after a lost acknowledgement without another cutover.

## Route records

The route schema accepts only Tantivy targets for `current` and `previous`, plus
the mutation lease. Records, journals and plans written before 2026-11-02 may
still carry `native_rollback`, `rollback`/`promoted_at` markers or a native
`previous`. The controller drops those fields when it reads them, so in-flight
operations keep matching their route; the next controlled write omits them. A
record whose `current` is native is rejected. Typeahead's route parser
reads only `version` and `current`.

## Automatic continuation

`automatic_refresh:true` continues the serving Tantivy route; see
[automatic refresh](automatic-typeahead-refresh.md) for the deployment, pins and
recovery. The dispatcher validates the current slot's exact admitted image,
observed guest and encrypted 20-GiB volume, strict source freshness and non-test
release proof. It then composes the checkpoint publisher, inactive-slot
preparation, guarded promotion and post-promotion retention.

The normal deployment uses the twelve-hour cadence documented above because a
checkpoint has a one-day coverage lifetime unless a genuine remote round renews
it. Keep this cadence until renewal is independently proven. The three-hour outer
worker budget bounds failure; it is not a promise that a refresh finishes in that
time. Do not introduce a second publisher clock or infer successful refreshes from
the presence of a schedule.

If the serving Tantivy slot fails three `/health` probes 20 seconds apart
(unreachable, not ready, or a non-JSON reply), automatic continuation still
builds and prepares a replacement in the inactive slot. An expired checkpoint,
which stops the slot, is the usual cause. The preparation journal records
`serving_unavailable`. Its corpus floor then comes from the `source_actor_count`
of the artifact admitted into the serving slot, read from R2, and its guards skip
the serving and public identity checks. Retiring an expired previous slot also
skips the current slot's freshness check. Route, lease, ownership and release
proof are still enforced, and promotion was already independent of the old
backend. A reachable slot that fails validation still fails the run.

An automatic pending journal resumes its exact request and immutable artifact
before another bulk export. Manual candidates remain fenced for operator review.
`resume_rollout:true` with no pending operation fails instead of starting a new
build. Promotion-response recovery uses the existing durable CAS journal. A route
or ETag change during publication or preparation aborts continuation; immutable
publication is retained and the newer route wins.

Cleanup runs only after confirmed promotion and public verification. A crash after
immutable publication but before preparation creates its pending record remains
a bounded recovery limitation: resume-only will refuse to invent a pending request,
and a later fresh cadence can perform another export. It does not change routing,
overwrite an immutable artifact, or imply complete crash recovery for that gap.

Offline verification uses actual HTTP and filesystem-backed R2 for route changes
during publication, pending-before-build resume, route/ETag fencing, resource
drift, a dead serving slot and a promotion that reads no native origin. Tests
below the independent release authorization gate do not manufacture a positive
production proof.

Cancellation reconciles a pending start against actual provider state. If the
owned machine is already stopped, it confirms that state and releases the
reservation without starting the machine merely to stop it again. Ownership,
previous-execution completion and current-route fences still apply.

Unconfirmed provider actions retain their intent and sanitized failure category
(HTTP status or transport exception type) in the preparation journal. Transition
timeouts report the machine, observed state and requested state alongside that
category. They do not erase the pending action or retry an uncertain mutation.

A start rejected with HTTP 412 is reconciled separately from an uncertain
transport failure: the controller verifies the same owned machine is stopped,
records the rejected attempt, and retries startup at most three times. This also
allows resume-only recovery of a previously journaled 412 without rebuilding or
allocating another machine. Every retry retains the route and configuration
checks. Other failed or ambiguous start requests still wait for their recorded
outcome; they are not blindly retried.

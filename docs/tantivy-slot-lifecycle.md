# Tantivy slot operations

The companion branch adds explicit `tantivy_operation` requests to the existing
`typeahead-index` cloud flow. Default native invocation and automatic refresh
remain unchanged. A preparation returns an immutable review plan; it never changes
`current`, `previous` or `native_rollback`. The operator must separately request
promotion after supplying the exact plan, current ETag and complete empirical
release receipt. Managed slot A has passed cloud publication, installation and
cached restart qualification on Fly using build `b1793346536-cp-7c8322df79`.
The immutable prepared plan is
`7f547a14b234180083cff854cdb5a00163faf214301f9a96467bb6b40624173b`;
production remains native. Two-slot retirement and automatic Tantivy refresh
have not been exercised against the live managed slots.

Only `typeahead-tantivy-a` and `typeahead-tantivy-b` are eligible. Every candidate
must carry owner `typeahead-snapshot-refresh`, role `tantivy-slot`, request/build
identity, pinned image digest, two shared CPUs, 1 GiB RAM and its own encrypted
20 GiB volume. Provider mount records may include name, encryption and size
metadata; identity still requires exactly one writable `/data` mount of the pinned
volume. Conflicting metadata, read-only options or extra mounts fail closed.
Serving/native/ingester machines are never modified. An unrelated
machine or volume fences preparation. The existing checksum-pinned Typeahead
installer performs installation and runtime qualification; this controller does
not implement another installer or uploader.

An explicit request takes this shape:

```json
{"tantivy_operation":{"operation":"prepare","build_id":"b1793236000-candidate","image":"registry.fly.io/typeahead-qual-20261029@sha256:<approved digest>","request_id":"<32 lowercase hex>"},"automatic_refresh":false}
```

The published artifact manifest and exact native source manifest determine all
source bindings. For `sqlite-committed-prefix`, the primary source build, receipt,
counts and watermark describe the raw checkpoint. Its independently validated
`rollback_native_anchor` names the retained native manifest/twin and their own
watermark. Deployment and restore fetch that anchor manifest; retention protects
both source builds plus the native twin and Tantivy bundle. It never expects a
native prefix artifact under the checkpoint build ID. The controller writes immutable deployment metadata under
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
it and cannot target its app; a native rollback remains allowed. A changed routing
head aborts preparation. Direct R2 edits or provider mutations outside this
controlled protocol cannot be globally locked by this controller.

`{"tantivy_operation":{"operation":"cancel","request_id":"<same request>"}}`
stops only the exact owned inactive candidate and retains its volume and artifacts
for inspection or same-request recovery. It cannot cancel a serving machine or
silently resolve an unknown create/delete. Current, previous and native anchor
artifacts, publisher latest source/twin and pending candidate source/twin/bundle
are protected from cleanup. Cleanup is fenced while a mutation lease exists;
retention identity is checked again before each deletion batch.

The default refuses to reuse a slot still referenced by `previous`. An explicit
`retire_previous_tantivy:true` additionally requires independently fresh/healthy
current Tantivy, a healthy native rollback anchor, complete non-test empirical
release proof, exact old ownership and immutable retained source/twin/bundle
metadata plus payload presence/size. Payload checksums are rechecked on actual
restore rather than downloading the entire corpus merely to retire a slot. The
old routing metadata remains until promotion, while the native anchor supplies
rollback during reuse. Retirement intent and exact old IDs are journaled before
stop/delete; only the same candidate can resume after failure.

Promotion is a separate explicit operation containing `plan_sha256`,
`expected_current_etag`, `release_receipt_key` and `release_receipt_sha256`.
Required proof gates are matched performance, full functional correctness, live
source/coverage, Worker/HTTP smoke, recovery and refresh lifecycle. Each must be
true with an evidence SHA256, bound to the exact binary, strategy, canonical rules,
image and final guest/volume budget. A test-only proof or preparation is rejected.
The existing full benchmark is a component result, not a substitute for these
release gates.

Before CAS, promotion rechecks actual provider/HTTP/source and the native anchor.
It releases the preparation lease, then uses the resulting ETag to install the
exact reviewed target. A competing lease or operator update invalidates that CAS.
Public verification checks engine/build/format/binary/strategy headers and the
canary, rather than build ID alone. Failed verification compensates only while the
same target and ETag remain current; operator changes win. A committed transaction
journal supports verification after a lost acknowledgement without another cutover.

## Automatic continuation after approved cutover

The local companion code now dispatches the existing compact
`automatic_refresh:true` flow according to the current route. With native selected,
the existing native build and refresh path stays in charge. With Tantivy selected,
the dispatcher validates the current slot's exact admitted image, observed guest
and encrypted 20-GiB volume, strict source freshness and non-test release proof.
It then composes the existing checkpoint publisher, inactive-slot preparation,
guarded promotion and post-promotion retention. It cannot perform the initial
native-to-Tantivy cutover. That remains an explicit approved operation.

Automatic continuation uses the existing default `snapshot_engine:native`
parameter as the dispatcher; explicit `snapshot_engine:tantivy` combined with
`automatic_refresh:true` remains fenced. Configure these metadata values for the
same publisher only as part of the approved transition:

- `INDEX_TANTIVY_SOURCE_REVISION`: exact 40-character producer revision.
- `INDEX_TANTIVY_ADAPTER_SHA256`: exact checksum-pinned adapter.
- `INDEX_TANTIVY_RELEASE_RECEIPT_KEY` and
  `INDEX_TANTIVY_RELEASE_RECEIPT_SHA256`: immutable complete release evidence.
- `ref:null`, or exactly the same immutable producer revision. A conflicting
  native tag or mutable branch is rejected before checkout.

No scheduler or deployment was changed by this implementation. The existing
publisher's two-day cadence exceeds the checkpoint's one-day coverage lifetime
unless a genuine remote round renews it. The unapplied cutover configuration must
therefore use the same cloud pool with a twelve-hour cadence until actual renewal
is proven, and a bounded worker budget around 7,200 seconds. These are configuration
requirements in the approval plan, not a build-time or availability estimate.
The existing publisher is paused only during the approved transition; no second
publisher clock is introduced.

An automatic pending journal resumes its exact request and immutable artifact
before another bulk export. Manual candidates remain fenced for operator review.
`resume_rollout:true` with no pending operation fails instead of starting a new
build. Promotion-response recovery uses the existing durable CAS journal. A native
rollback or route/ETag change during publication or preparation aborts continuation;
immutable publication is retained and the newer route wins. Initial promotion
also requires a fresh native rollback overlay. Availability rollback retains its
separate degraded-native contract.

Cleanup runs only after confirmed promotion and public verification. A crash after
immutable publication but before preparation creates its pending record remains
a bounded recovery limitation: resume-only will refuse to invent a pending request,
and a later fresh cadence can perform another export. It does not change routing,
overwrite an immutable artifact, or imply complete crash recovery for that gap.

Offline verification uses actual HTTP and filesystem-backed R2 for rollback during
publication, pending-before-build resume, route/ETag fencing, source-ref conflicts,
resource drift, and stale-native promotion refusal. Tests below the independent
release authorization gate do not manufacture a positive production proof.
Deployment and unattended execution remain deferred until cutover approval.

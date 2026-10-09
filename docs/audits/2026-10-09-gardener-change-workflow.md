# Gardener production change-workflow audit

## Execution evidence

The operator sent Phi the real private request to fix bot's stale full-atlas
cache. No operator-account posts or approvals were manufactured.

| Stage | Production evidence | Result |
| --- | --- | --- |
| Proposal | `0b834d6f-cd25-42de-b16e-4cbd806a2445` | Completed on Exe; bridge idempotency key retained; Gardener published a patch. |
| First review | `d325a02e-6580-4c7b-aabe-cb9806ed4307` | Automatically triggered Phi; genuine request-changes on cache race, disappearing overlay, and superficial tests. |
| Recovery | `bae6c4a5-81a7-4268-abe7-97cc01cb2212` | Watcher recovered the review missed by the stream bridge after its ten-minute grace period. |
| Initial revision attempt | `f755fe75-475d-4c97-89d8-53208d0b3731` | Failed closed: judge confused trusted Gardener instructions with instructions to the judge. No patch published. |
| Corrected revision | `9a639123-92a1-44bb-872e-01d9fbc0ccdb` | Revised on Exe; 19 model turns; actual file edits; a new self-contained patch round. |
| Second review | `1c12bc49-9f85-4fae-b6b8-9db2e3b0a39c` | Automatically triggered; found a null-response/error-state regression, but hosted MCP incorrectly bound the comment to round 0. |
| Review after MCP repair | `9b242a83-f415-4be0-81e8-cc6bb6ead4f5` | Phi published request-changes with the correct CID and round index. The merge controller honored it. |
| Second revision attempt | `a7a2fbea-d5f8-41bb-9440-cd579d152fd0` | Failed closed: judge mistook the code-review verdict label for an instruction about its screening verdict. |
| Revision after screening clarification | `2a8fe218-751a-40d9-8e6f-53c0fe902ffb` | Revised on Exe; published round 3, including null-response failure handling and regression coverage. |
| Final review | `3fb61007-a252-462e-b12b-df15334392be` | Phi approved round 3 with its exact CID. |
| First full test | `e3894210-32b6-480a-b1e8-237da300a016` | 966 Python tests passed; unrelated generated-image eviction test failed. Frontend checks did not run; controller returned Tests-Failed. |

Pull: `at://did:plc:7vx7exykq2zfxjxxejovrymi/sh.tangled.repo.pull/3mxge6p7esx3q`.
First review: `at://did:plc:65sucjiel52gefhcdcypynsr/sh.tangled.feed.comment/3mxgeanz4zknj`.
The later incorrectly bound review is `3mxgfbij6tkvl` on that reviewer's
`sh.tangled.feed.comment` collection. It has not been edited or treated as a
valid review of round 2.
The correctly bound replacement is `3mxgfpdcgibyh`; its subject CID matches
the pull at that revision and its zero-based round index is 1. The operator replayed
that real PDS comment through the bridge's normal delivery function, which
emitted the event and recorded the comment in the handled journal.

## Corrections deployed

- Enabled Phi proposal dispatch and moved `pi-pr` onto `gardener-exe`.
- Reconciled the previously deployed Gardener branch into main. A main release
  at 06:33 UTC had registered old configuration, reverting proposals to home-pool
  and Exe jobs to wheel uploads. Main now declares the live immutable images;
  production registration was exercised again with those declarations.
- Added build-time Bun/Node to the isolated test image. Bot patches now run
  Python tests, frontend type checks, and frontend tests.
- Screen revision feedback and all untrusted metadata separately from the fixed
  agent instruction scaffold, as proposal dispatch already does. The live judge
  allowed the genuine review and blocked credential exfiltration embedded in
  metadata. It remains enabled and fails closed.
- Clarified that role descriptions and code-review verdict labels are source
  data; the judge must classify requested actions rather than writing style.
  Five live cases allowed the actual review and ordinary coding instructions,
  and rejected metadata exfiltration, out-of-workspace service changes, and
  fake-authority requests for API keys. These are observed model decisions,
  not proof that a probabilistic classifier never misclassifies an input.
- Built the corrected revision flow on the current publishing-helper image.
  Import verification caught missing current reviewer-binding helpers in the
  older revision image before deployment.
- Shipped the existing Tangled MCP revision-binding fix on main (`fed68a6`).
  Horizon deployed archive `61528148f28733f2e49bfe5b9eab40c7c669979458e4b558f3c02d698bfeca53`.
  Live schema exposes `expected_cid`; pull reads return CID; verdicts require
  it; comments use the actual latest round index. Local MCP checks against the
  real pull rejected stale patch reads and stale comments before login/write.
- Updated the trusted merge-controller wheel to the checked-in async test
  handoff and strict result-row validation. Installed flow SHA-256 matched
  `de518a0204a9fe9a4c45b0dd9f6cd5b202c541925bdef22348e0a0b67157002c`.
  The wheel SHA-256 is `a8b8b04c4f1a5ae0aa79014615eb44f2bdf28737037593bedb736cc46a1c58c0`;
  the production command is pinned in `prefect.yaml`. Public cloning and tests
  now precede merge-key preflight. The dedicated merge key is unregistered;
  no replacement identity or new permission was granted. SSH preflight remains
  before human approval, and Prefect Resume remains required before merge.

Image digests and source-override provenance are in `deploy/gardener/images.json`.
Mps checks: 621 tests passed locally and in CI; type/lint checks and 13 hub tests
passed. CI initially exhausted file descriptors; its limit is now 8192, with
first-failure reporting. Tangled MCP checks: 35 tests and lint/format checks passed.

## Runtime and safety observations

The disposable frontend-runtime VM passed uid-2000, empty supplemental groups,
zero capabilities, cleared orchestration credentials, inaccessible `/root`, and
absent inference socket checks. UV, Bun 1.4.0, and Node 24.18.0 executed inside
that sandbox. The VM was deleted. No inference integration was attached to it.
The existing test sandbox retains public network access for package downloads;
this change does not establish a stronger network boundary.

The proposal and revision-attempt VMs were deleted. Exe's authoritative
integration listing then showed no `llm` attachments; its VM listing contained
only the private registry VM. Detach-denies-inference behavior was separately
established by the October 8 live probe. No retained job VM or integration was
found at this checkpoint.

Proposal VM creation/bootstrap/service start: 3.082 / 2.753 / 0.785 seconds.
Corrected revision on a fresh image: 32.585 / 2.878 / 0.759 seconds. Its inside-VM
image verification took 1.876 seconds. These are observations, not p95s: the
fresh-image creation cost is material, while bootstrap performs verification,
not Python or dependency installation. Repository test dependencies remain
separate from bootstrapping the prepared agent runtime.

The stream comment bridge remained connected but delivered no review events;
a bounded replay also found none. The authoritative PDS watcher recovered the
first review. This was operator-triggered recovery, not proof of healthy fast
stream delivery. The normal hourly watcher remains the safety net.

Final test execution passed with the exact patch/base hashes recorded below.
Final bot merge remains behind human Prefect Resume;
no merge or bot deployment is authorized by this exercise. Existing broad Exe
SSH identity and inference timeout/spend limitations remain in the October 8 audit.

## Final test retry and inference restriction

The sprawl workstream reproduced the unrelated baseline eviction failure and
shipped bot commit `a67508c6995d85be41178945c329ee149a25fe7a`. The original
approved atlas patch remains unchanged. Controller
`5f726997-fab6-4e66-acbb-43d140af84c5` dispatched test
`c28772dd-9909-4342-aba1-acbecca65392` against the corrected baseline.

Safety inspection found the worker attached inference to test VMs as well as
agent VMs. Worker release `35fba2601c49eaacdbedb0da85e2ed7b7ad472a0` restricts
attachment to investigate, pi-pr, and autofix-revise deployment IDs. Unknown,
ad-hoc, and test deployments receive none. It was installed after checking no
owned VMs remained; the service is active on the verified release. All 61
worker/runtime tests passed. On the actual retry test VM, provider inspection
showed no llm attachments and a fresh Anthropic request returned HTTP 403.

Approved round 3 CID:
`bafyreiezf6h3fadxkau2vxnku4yw6fxj47zsvteiy6qhuygeqxrfdnf3we`.
Phi approval comment: `3mxggbcevpl6b`, zero-based round 2.
Patch SHA-256: `854f31e943c6874aac727658dde8e5ed6a9246c27c951b2d2381f75215962576`.

The final test receipt reports `passed: true`, base
`a67508c6995d85be41178945c329ee149a25fe7a`, and the exact approved patch hash
above. It ran 968 Python tests (30.93 seconds), Svelte checks (zero errors or
warnings), and 22 frontend tests. Flow execution took 50.60 seconds. Worker
bootstrap took 2.728 seconds, including 1.723 seconds of image verification;
service start took 0.774 seconds. Cleanup completed at 07:25:35 UTC. The worker
logged a harmless attempted detach of the intentionally absent llm attachment;
VM deletion still succeeded. Avoiding that redundant detach is follow-up cleanup,
not evidence of a leaked attachment.

The complete proposal → review → revision → review → revision → approval →
test path has now run against real production services and Exe VMs. This is
not evidence that the entire path is unattended: recovery triggers and release
repairs were necessary, and the stream bridge fast path remains degraded.

Controller `5f726997-fab6-4e66-acbb-43d140af84c5` ended `Blocked` with
“tests passed; merge key cannot read the knot,” as expected from the existing
unregistered dedicated key. Final provider inventory contained only
`gardener-registry`, and the llm integration had no attachments. No job VM remained. No final merge was attempted.

Worker safety-release CI passed all 621 tests in 181.07 seconds, plus lint and
type checks, before production deployment registration. The final test receipt
and local task-result handoff were retained; the sprawl thread was notified.

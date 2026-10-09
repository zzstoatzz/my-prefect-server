# Gardener change-workflow readiness

## Verified on October 9

- Phi's bridge accepts an explicitly configured proposal deployment. The
  production service enables `pi-pr` (`ec0cc6dc-bed2-4b93-80ba-41c15cb2a5cb`).
  Its existing owner gate and request identity checks remain intact.
- `pi-pr` now uses the isolated `gardener-exe` runtime. Its flow and publishing
  module bytes were checked against the existing investigation image before reuse.
- The test image adds Bun 1.4.0 and Node 24.18.0 at build time. Bot patches run
  Python tests, frontend type checks, and frontend tests. Image import and binary
  checks passed during the actual Docker build; this is not a live job result.
- The full Python suite passed: 621 tests. The bridge test uses real local HTTP
  transport and verifies proposal dispatch, stable retry identity, and rejection
  of caller-supplied infrastructure overrides.
- Investigation `1a2eac21-bb8f-428b-b321-b43b6503ba77` completed on Exe with
  27 successful tool calls and VM deletion. It found a useful atlas cache issue;
  this investigation does not establish proposal/review/revision/test coverage.

## Deployment drift

A main-branch release registered all deployments from source that lacked the
Gardener changes already deployed from `codex/dagster-hub`. At 06:33 UTC it
restored the proposal job to `home-pool` and Exe jobs to uploaded wheels.
Image-mode configuration was restored through the API. The release branch
reconciles the existing Gardener commits with main so future registrations
preserve the declared runtime. Immutable images and override provenance are in
`deploy/gardener/images.json`.

## Still required

The full production change loop is **not yet exercised**. The reserved task is
bot's stale full-atlas cache after a newer preview generation. Phi must receive
the operator's actual private request; this assistant must not manufacture that
request, use the operator's main account, or invent a review requiring revision.
Only genuine review findings should drive a revision. Final merge remains gated
by the existing human Prefect Resume action.

An isolated VM check of the new test image is pending HeavyPad Tailscale SSH
reauthentication. No alternative credentials or authentication bypass were used.
No new inference integration was attached for that pending preflight.

## Safety review scope

No identity, review, or merge approval gates were relaxed. The test sandbox still
runs as uid 2000 with cleared environment and no merge credentials or inference
socket. Its existing public network access supports dependency installation;
this change does not establish a stronger network boundary. The new frontend
commands execute untrusted repository code inside that same test sandbox.

The post-execution audit must still verify actual run identities, exact tested
base/patch hashes, review rounds, VM cleanup, and inference detachment. Existing
provider credential and timeout limitations remain documented in the October 8
audit. Readiness checks are not a substitute for that audit.

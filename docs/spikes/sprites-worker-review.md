# Sprites worker comparison — 2026-09-09

The comparison found substantial lifecycle gaps in the first implementation.
The revised worker now separates submission, observation, and cleanup, with
25 passing local tests. It is still not ready for smoke testing: service crash
recovery and Pi/engine isolation need further work. Sprites remains the sole
planned execution backend.

Reviewed local sources, without modifying either reference repository:

- Prefect `ce79dd3d6c`: `src/integrations/prefect-kubernetes/prefect_kubernetes/worker.py`
  (`run`, `_initiate_run`, `prepare_for_flow_run`, `kill_infrastructure`),
  `observer.py` (`_mark_flow_run_as_crashed`), and observer tests.
- Same Prefect revision: `src/integrations/prefect-aws/prefect_aws/workers/ecs_worker.py`
  (`run`, `_initiate_run`, credential preparation, cancellation), and
  `observers/ecs.py` (`mark_runs_as_crashed`).
- Nebula `ec54ebb3d`: `src/prefect_cloud/work_pools/workers/managed/worker.py`
  (submission, status checks, cleanup, billing handoff, AWS env delivery),
  `managed_pid.py`, `envfile.py`, and PID/envfile cleanup tests.
- Sprites Python SDK `5551e857c902dfa29a56c01207cc53f37fe14796`:
  `src/sprites/websocket.py`, `async_sprite.py`, `async_exec.py`.

The referenced worker and managed-execution files were clean in their local
checkouts. The comparison follows the actual methods, including where their
docstrings still describe older blocking behavior.

| Concern | Reference behavior | First Sprites implementation | Required change |
| --- | --- | --- | --- |
| Submission | Kubernetes and ECS create infrastructure, report its ID, and return success for submission. Their observers detect infrastructure failure. | `run()` waits for a single exec stream and returns the process exit code. | Separate accepted submission from execution outcome; add an observer that can rediscover runs after worker restart. |
| Durable identity | Kubernetes uses namespace/job; ECS uses cluster/task ARN. Nebula stores provider ID and retry count independently of the submitting process. | Name contains flow-run ID and run count, but no persisted session identity or submission phase. | Persist Sprite identity, execution attempt, and session identity before treating submission as accepted. Scope ownership to pool and attempt. |
| Uncertain submission | Nebula records retry attempts and only retries classified infrastructure-submission failures. | A lost create response can orphan a Sprite; no reconciliation exists. | Reconcile deterministic identity and observed submission state. Never start a second command just because observation failed. |
| State reconciliation | Observers read the current Prefect state and use state proposals. Kubernetes protects paused, scheduled, final, cancelling, and replacement-job cases. | Cleanup runs on every exit from the local `run()` context. No restart-safe state reconciliation. | Preserve newer attempts, suspend/resume, human approval, cancellation, and completed flow states when interpreting infrastructure outcomes. |
| Secrets | Kubernetes can reference Secrets; ECS can reference secret ARNs. Nebula's AWS envfile is SSE-KMS encrypted and removed independently. | `configuration.env` goes through SDK exec parameters. The SDK serializes these into the WebSocket query string. | Never send credentials in exec URLs. Deliver environment through a protected channel with explicit deletion. Use a credential block/configuration model for the worker token. |
| Agent isolation | These workers isolate flow infrastructure; that alone does not separate an agent from its flow engine. | Pi/engine privilege separation has not been implemented. | Verify Pi cannot read Prefect, Sprites, publishing, or Tailscale credentials. Keeping only the Sprites token off the machine is insufficient. |
| Cancellation | Kubernetes deletes with a grace period; ECS requests task stop. Both map missing infrastructure to Prefect's typed exception. | Deletes the Sprite, with a broad name-prefix/label ownership check. | Validate exact ownership, distinguish stop from artifact cleanup, and make repeated cancellation safe. |
| Cleanup | Kubernetes has Job/Secret ownership; ECS observer handles task-definition deregistration. Nebula cleanup is a separate operation and retains the PID when cleanup fails. | A shielded `finally` handles local cancellation, but hard worker death bypasses it. Cleanup errors can obscure the execution result. | Rediscover cleanup obligations, preserve failure diagnostics, retry cleanup independently, and delete only after required artifacts are collected. |
| Cost | Nebula hands billing off after teardown, using the actual provider submission ID. Billing failure cannot abandon infrastructure or credentials. | No usage record or Evergreen integration. | Record provider identity and observed lifecycle; obtain available usage data and label estimates/unknowns accurately. Cost collection must not block cleanup. |
| Configuration | Kubernetes/ECS expose distinct configuration and variables models, inherited attribution, credential blocks, and nonblocking submission hooks. | Generic `Any`, hardcoded Python/Prefect bootstrap, no variables model or submission hook. | Use normal integration models and metadata, preserve attribution, and explicitly define supported deployment/bundle behavior. |

Do not copy Nebula's multi-tenant project allocation, billing relay, or Redis
topology into this integration. The useful contract is independent, recoverable
submission, observation, and cleanup. The Sprite API should provide infrastructure
facts; Prefect should retain orchestration authority.

## Gates before smoke testing

1. Replace the blocking lifetime with submission plus independently recoverable
   observation and cleanup. A worker restart must not duplicate execution.
2. Remove credentials from exec query parameters and establish the Pi/engine
   boundary. Do not snapshot credential-bearing environments as reusable bases.
3. Add lifecycle tests for duplicate/uncertain submission, worker restart, temporary
   API failure, missing infrastructure, paused/cancelling/final states, stale
   attempts, and cleanup failure after a successful run.
4. Test the generated work-pool template, attribution, credential block resolution,
   and the actual SDK call shapes. Mock-only happy paths do not validate them.
5. Preserve artifacts and record runtime/cost evidence before deletion, with
   cleanup independently retryable.

## Evidence so far

- The current local suite passes 25 tests. Worker tests replace provider I/O;
  supervisor tests run real local subprocesses. Neither proves live Sprite
  service behavior or deployment readiness.
- The published `sprites-py==0.6.0` lacks the documented async client. The spike
  pins the inspected Git revision instead; this requires resolution before release.
- `phi-workflows-probe` exists in organization `nate-nowack`; the API reports warm,
  zero running Sprites, and `last_running_at: null` before the infrastructure
  preflight below. No Phi smoke workload has run.
- No Fly Machine app exists for this spike. The heavypad experimental container
  and image were removed; its production `prefect-home-worker` remains active.
- CLI authentication unexpectedly printed its newly minted Sprites token.
  Permission to revoke and replace that token has been requested. Future token
  creation output must be captured privately.

The next implementation should satisfy these gates before exercising Pi through
Aperture and the full Phi patch/review/revision cycle.

## Implementation progress after comparison

- Submission now records deterministic pool/run/attempt identity in Prefect,
  installs the supervisor, creates the service, and returns. A separate observer
  rediscovers resources by pool ownership after worker restart. Uncertain create
  responses reconcile the same name rather than allocating another resource.
- The observer protects newer attempts and final, paused, scheduled, and
  cancelling states. A confirmed provider 404 can crash a current pending/running
  run; network failures leave it untouched. State changes use proposals.
- Cancellation stops the service without deleting diagnostics. An unknown service
  state is not accepted as confirmation that cancellation succeeded.
- Diagnostic artifacts precede deletion; artifact failures retain infrastructure
  for a subsequent cleanup attempt. Billing collection is not implemented.
- `SpritesCredentials` is exposed in the generated work-pool template. Launch
  configuration travels through stdin instead of SDK exec environment parameters,
  which serialize into the WebSocket URL. The supervisor configuration is stored
  under a root-only directory and removed on completion.
- The supervisor uses an execution lock, atomic outcome writes, and a timeout.
  Local subprocess tests verify completed attempts are not replayed on restart.
- Current validation: 25 tests pass in `test_sprites_worker.py` and
  `test_sprites_runtime.py`. No remote workload ran during this comparison.

## Remaining gaps before the smoke test

1. The flow engine now inherits the execution lock. A real subprocess test kills
   the supervisor and verifies a replacement cannot declare the surviving engine
   exited or replay it. Descendant-process cleanup and timeout enforcement after
   supervisor death remain unresolved. Also handle a dead service whose persisted
   phase remains running; verify actual Sprite service restart behavior.
2. Establish and test the separate unprivileged Pi identity. Root-only launch
   files do not by themselves protect the flow engine's environment from Pi.
3. Enforce pool-scoped credentials or persist an observer-resolvable credential
   reference. Currently submission allows configuration the pool observer might
   not be able to recover. Define supported bootstrap and bundle behavior.
4. Validate service API acceptance and typed outcomes. Check attribution beyond
   ownership labels. Missing-run records and indefinite diagnostic-upload failures
   still need bounded resource handling; repeated artifact writes can duplicate.
5. Record provider lifecycle and usage evidence for Evergreen. Runtime duration
   alone is not an actual provider bill; unknown usage must stay unknown.

The Kubernetes observer is the stronger reference for state protection: unlike
its explicit cancelling check, the inspected ECS early-return guard lists final,
scheduled, and paused states. The Sprite worker deliberately includes cancelling.
Nebula's billing handoff is failure-isolated from teardown and credential cleanup;
that separation should remain when Evergreen accounting is added.

Token replacement approval remains pending. No smoke test, production rollout,
or public repository mutation was performed as part of this comparison.

The observer also re-reads the flow run after provider inspection before making
a state proposal. A regression test advances to a replacement attempt during that
inspection and verifies it is preserved. This reduces the stale-read window; it
is not a compare-and-swap guarantee across the Prefect/provider boundary.

## Infrastructure preflight after comparison

Read-only commands on the existing `phi-workflows-probe` confirmed Linux
6.12.105-fly, Python 3.13.7, passwordless root sudo, working mount/PID namespaces,
and writable cgroup v2 controls. `uv`, `bwrap`, `systemd-run`, and `tailscale`
were not found on PATH. No agent or flow was launched.

The missing `uv` invalidated the proposed bootstrap. Installation now creates a
root-owned Python virtual environment and installs the configured requirements
there. The runtime prepends its bin directory when starting the flow command.
Abandoned-bootstrap detection allows 600 seconds, exceeding the 300-second
installation request timeout. These changes pass the 25-test local suite and
Ruff; actual package installation on a Sprite remains unverified.

## Verified cgroup cleanup

The worker now requires a dedicated cgroup v2 execution group. The supervisor
moves the engine into it before exec, and kills the group and confirms it is
unpopulated before recording exit. On restart it cleans up the persisted group
before reporting an interrupted attempt. This follows the Linux kernel
[cgroup v2 contract](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html).

`tests/sprites_lifecycle_probe.py` ran successfully as root on the disposable
`phi-workflows-probe` Sprite. Two real process scenarios passed: detached-child
cleanup after engine exit, and surviving engine/descendant cleanup following
SIGKILL of the supervisor and invocation of its replacement. Both verified an
empty cgroup and removal of the saved environment. The probe removed its temporary
directories and cgroup. Lease API calls were stubbed; this does not validate the
Sprite service restart policy or a full Prefect deployment.

The previous inherited-lock-only approach remains a local macOS test facility;
worker submissions always request the Linux cgroup. Remaining lifecycle work is
observer recovery when the service stays dead, cancellation grace behavior, and
bounded cleanup if no observer returns. Pi isolation and Evergreen integration
are still unfinished. Local suite: 25 passed; Ruff passed.

## Lease and agent-boundary preflight

The live image rejects `sprite-env api`; that runtime call was incorrect. The
runtime now uses curl over `/.sprite/api.sock`, and create/delete calls from the
actual runtime module passed on the probe. A five-minute lease is refreshed each
minute for long executions rather than requesting a hold beyond the API's
one-hour maximum. Local tests still pass (25); Ruff passes.

The management socket is world-writable on this image, so UID separation alone
is insufficient. Installed Ubuntu bubblewrap 0.11.1 on the probe. User-namespace
mode failed to mount proc; root-created mount/PID/network/IPC/UTS namespaces plus
`setpriv` succeeded. The test process ran as UID/GID 2000, with supplementary groups
and capabilities dropped and no-new-privileges enabled. An allowlisted filesystem
exposed /usr, fresh /proc, /dev, /tmp, and library symlinks; checks confirmed the
management socket and trusted engine directory were absent.

This is a boundary preflight, not yet a Pi launcher. The agent still needs a
writable checkout, the Pi executable, and a controlled Aperture connection through
its separate network namespace. No Phi flow has run on this infrastructure.

## Reusable Phi agent launcher

Added `mps.pi_sandbox.sandbox_command` and the explicit live probe
`tests/sprites_agent_boundary_probe.py`. The launcher exposes only system tools,
a trusted tool installation, and dedicated writable checkout/home directories.
It unshares PID/network/IPC/UTS/cgroup namespaces, clears the environment, drops
UID/GID to 2000, clears groups and capabilities, and sets no-new-privileges.
A single optional inference Unix socket can be mounted; no general network route
is provided. It is not yet wired into `mps.pi.run_pi`.

The real module passed the live Sprite probe: writes to checkout/home succeeded,
a workspace symlink could not reach a host canary file, the Sprite socket and
engine directory were absent, a fake Prefect environment credential was absent,
setuid-to-root failed, effective capabilities were zero, and the network namespace
had no routes. The initial run exposed inaccessible parent directories; explicit
0755 home/opt mountpoints fixed it. Temporary probe directories were removed.
This verifies these specific boundaries, not resistance to kernel exploits or
completion of the Aperture/Pi integration.

## Pi startup verified inside the boundary

Installed Pi 0.84.4 under `/opt/phi-agent/pi` and copied the observed Node
24.18.0 executable into `/opt/phi-agent/node/bin`. This avoids mounting the
Sprite's language shim, which accesses `/.sprite` and writes shell configuration.
The installation is root-owned and mounted read-only in the agent namespace.

`tests/sprites_pi_startup_probe.py` invoked the real Pi CLI through
`mps.pi_sandbox.sandbox_command` on the Sprite. It returned version 0.84.4 and
listed the custom aperture provider with `openai/gpt-5.6-luna`. The temporary
agent home used Pi's documented models.json interface and was removed afterward.
The displayed context/output settings are configured limits, not independently
verified provider capabilities. No inference request was made. Inference transport
and production flow wiring remain incomplete.

## Inference bridge implementation

Added `mps.inference_bridge.inference_bridge`, a per-attempt Unix-socket server
that accepts only POST /v1/chat/completions for the configured model. It builds
upstream headers itself, disables HTTP redirects and environment proxy settings,
bounds request bodies and output token settings, and enforces an attempt request
count. Successful responses are streamed without buffering the entire response.
It exposes request/success counters; these are not usage or billing records.

Six local tests using real HTTP and Unix sockets passed: streaming/header
isolation/output cap, three path/model rejection cases, request cap, and redirect
refusal. Ruff passed. Tests use a fake upstream and fake credential values; the
real Aperture connection is not yet wired. Runtime limits are not a dollar budget.
Open bridge handlers use socket timeouts, but active response cancellation on
context exit still needs refinement for long streams.

## Bridge verified through the live namespace

`tests/sprites_bridge_probe.py` passed on the Sprite using the actual launcher
and inference bridge modules. A process without a network route connected through
the mounted inference socket; the trusted bridge called a fake upstream bound on
the host loopback and supplied its own fake authentication header. Temporary
socket/directories were removed. This proves cross-namespace socket transport;
it is not an Aperture inference result.

Installed Tailscale 1.102.3 from its official static archive, verifying the supplied
SHA256 checksum. Created the `phi-tailscale` Sprite service with state and control
socket under root-only `/var/lib/phi-tailscale`. Current state is NeedsLogin.
The enrollment request names `phi-workflows-probe`; its link was sent to Nate.
The Mac is locked, so browser enrollment could not proceed. No other tailnet
settings or existing devices were changed. Production automatic enrollment of
per-run Sprites is still unresolved.

## Pi transport path verified

Added `mps.pi_relay`, a credential-free in-namespace launcher that starts socat
on loopback:8888 forwarding only to the mounted inference socket, waits for
readiness, invokes Pi, and stops the listener on exit. Installed Ubuntu socat
1.8.1.1 on the probe; copied the relay into the read-only agent tools installation.

The expanded live `sprites_bridge_probe.py` passed both curl and actual Pi calls
through the boundary. Pi consumed the fake upstream's SSE chat-completion stream
and returned `pi-bridge-ok`. This validates transport and parsing, not model
inference: no request reached Aperture. The probe tailnet remains NeedsLogin.
The existing production `run_pi` call path has not yet been switched over.

## Superseding network decision: no per-Sprite enrollment

Nate rejected coupling ephemeral executions to tailnet enrollment. Deleted the
probe's `phi-tailscale` service, verified no tailscaled process remained, and
removed the task-created state directory and binaries. The probe never enrolled;
the previously sent login request is abandoned and must not be completed.

Aperture's documented external clients still authenticate with an embedded
Tailscale node. The required architecture keeps that identity on long-lived
infrastructure and exposes a restricted HTTPS inference endpoint with per-run
credentials. Heavypad's existing HTTPS Serve configuration is tailnet-only and
points to port 6555; it was inspected, not changed. Its production worker remains
active. No public inference endpoint has been deployed.

The new, not-yet-wired `pi_execution` runner now requires PHI_INFERENCE_URL (HTTPS)
and PHI_INFERENCE_TOKEN; it has no default direct tailnet IP. The token remains in
the trusted bridge and is not put into the Pi environment. Token issuance,
server-side validation/expiry, and deployment of that long-lived endpoint are
still required. Ruff passes for the new runner; its full call path is unverified.

## Durable run grants and authenticated listener

Added `inference_grants.InferenceGrants`, a SQLite-backed capability store. Tokens
are generated randomly and only their SHA256 hashes are stored. Each grant binds
an attempt, model, expiration, request limit, usage count, and revocation state.
Request consumption is one conditional SQL update, so concurrent callers cannot
exceed the count. Reissuing the same attempt fails instead of resetting its limit.
Idempotent recovery of an issued token still needs integration with submission.

The inference bridge now also supports a TCP listener, refusing to start that
listener without the grant store. It authenticates and consumes each request's
grant before proxying. Existing Unix-socket behavior remains the internal Sprite
hop; these are two ends of one execution path, not alternate execution backends.

Twelve local tests pass across bridge/grants, including real TCP authorization
checks, persistence after reopening the store, model/expiry/revocation checks,
concurrent limits, and rejection of unauthenticated listener configuration.
Ruff passes. The service has not yet been deployed or exposed over HTTPS.
Request counts are not dollar budgets or actual usage bills.

## Heavypad ingress deployed and real Aperture request verified

Deployed the three stdlib inference modules to `/home/stoat/phi-inference/src/mps`
and enabled `phi-inference.service` in stoat's user service manager. Linger was
already enabled. The service listens on 127.0.0.1:8999, uses a private SQLite grant
store, and targets the existing Aperture gateway. The repository contains its unit
and module entrypoint. The production Prefect system service remains untouched.

Unauthenticated POST returns 401. A fresh one-request, 60-second grant successfully
forwarded an actual Aperture request to `openai/gpt-5.6-luna`, returning the expected
`phi-ingress-ok`: 13 prompt tokens and 8 completion tokens. The grant was revoked
in finally. No credential was printed or written to a test artifact. Evidence is
in `evidence/inference-ingress.json`; provider cost was not included in the response.

The attempt to enable Funnel on separate HTTPS port 8443 was denied by Tailscale's
local permissions, and passwordless sudo is unavailable. Nate was sent the exact
admin command required. No Funnel configuration was changed; the existing port
443 tailnet-only service is separate. External Sprite access remains pending that
command. This is the real ingress-to-Aperture leg, not yet a full Pi/Phi cycle.

## Evergreen draft prepared

An isolated Evergreen checkout at `/tmp/evergreen-phi-spike` now adds the probe
and live heavypad ingress to Phi's resource inventory, reserves the exact
`sprites:phi-workflows-probe` cost key, and documents unmeasured costs and rollout
limitations in `docs/phi-workflows.md` and COSTS.md. Seven inventory/attribution
contract tests pass. No UI verification or publication is claimed. No provider
billing amount has been invented, and automated collection remains unfinished.

The latest heavypad check still shows only the original port 443 tailnet service;
HTTPS port 8443 forwarding remains pending the requested admin action. The
inference user service is active.

### HTTPS ingress and real Sprite inference verified

Existing root SSH access to heavypad resolved the operator-access issue without
user interaction. Funnel on HTTPS port 8443 now forwards to the authenticated
loopback inference service; the existing port 443 service is preserved. A request
from the Sprite without credentials returned 401.

The actual `run_isolated_pi` runner on phi-workflows-probe then called Aperture
through the Unix bridge and heavypad HTTPS endpoint, returning exactly
`phi-live-ok`. Its short-lived grant consumed one request and was revoked in
finally. The token traveled through captured subprocess pipes and stdin, never
command arguments or logs. No Sprite tailnet enrollment was required. This
supersedes earlier pending-HTTPS statements.

The full Prefect worker submission and Phi review/revision cycle remain unverified;
the new runner is not yet wired into existing flows. Provider charges for this
request were not returned and remain unmeasured.

### Phi caller integration and real code revision

The shared mps.pi.run_pi entrypoint now calls run_isolated_pi exclusively.
Autofix, revision, and manual Pi flows use Aperture; Pi no longer accepts a caller
environment. Provider/model values outside the scoped Aperture model fail before
launch. The trusted prompt judge still uses its existing Anthropic credential.
Production deployments have not been changed. Thirty-one focused tests passed
covering this boundary and the existing revision/merge behavior.

The live sprites_real_revision_probe started with a failing retry-budget function.
Pi received concrete review feedback and edited the file through its real tools.
Independent assertions, passed outside the agent-writable source tree and executed
inside the namespace, passed afterward. Five real Aperture requests were consumed;
the grant was revoked. This verifies coding and validation, but does not substitute
for the requested real Phi review/event/revision cycle.

The worker's configured Prefect endpoint is already HTTPS at
prefect-server.waow.tech/api; connectivity must be checked from the Sprite before
a real worker submission. Worker-side grant issuance and automatic installation
of Pi tools remain to be integrated.

### Real Prefect worker submission verified

The spike worker submitted flow run 60080a42-2180-48ca-997f-10e1e6af41ba
to pool phi-sprites-spike on the existing Prefect server. Its dedicated Sprite
prefect-ed3e4f65-60080a42218048ca997f10e1e6af41ba-r0 executed the Prefect
engine and returned phi-worker-ok. Prefect reached Completed, the supervisor
recorded exit code 0, and the observer deleted the Sprite after its diagnostic
retention interval. The verification worker then stopped.

Live testing found and fixed three assumptions missed by mocks: sudo selected
a system Python without ensurepip (installation now uses Sprite-provided Python);
HTTP creation conflicts are plain SpriteError (recovery verifies exact existing
identity and ownership); and service 404 responses use APIError (normalized at
the provider boundary, with other failures propagated). Bootstrap retries clear
an incomplete virtual environment. Twenty-nine worker/runtime tests now pass,
including regressions for the observed SDK error behavior; Ruff passes.

The first credential attempt used the keychain's encoded representation and
failed authentication before Sprite creation. The existing value was decoded in
memory; no token rotation or token transfer to the Sprite occurred. Retries used
the same flow ID and Sprite throughout.

This smoke flow validates the worker lifecycle, not automatic Pi provisioning
or the live Phi review cycle. Actual provider cost is still unmeasured.

### uv bootstrap supersedes the pip workaround

Sprite bootstrap now installs pinned uv 0.12.12 through Astral's versioned
installer into /usr/local/bin, without modifying shell profiles. uv provisions
Python 3.13.7 and creates the execution environment; uv pip install installs the
worker's requested packages. The supervisor starts with that environment's Python.
The old python -m venv / python -m pip path is removed. Bootstrap identity includes
the toolchain revision so an older prepared payload is not silently reused.

Fresh flow 8e7ffc9a-563c-4867-8b68-f873c956ddf5 reached Completed with exit 0
and logged uv 0.12.12, Python 3.13.7, and phi-worker-ok. This validates the installed
toolchain and actual Prefect execution on a newly created Sprite. The official
installer is downloaded with curl as documented by Astral; a Python urllib
download received HTTP 403 and was replaced before successful bootstrap.

References: https://docs.astral.sh/uv/guides/integration/docker/ and
https://astral.sh/uv/0.12.12/install.sh.

### Phi request interface recovered

Bot commit 129843c retired propose_code_change on September 2. That tool queued
pi-pr/pi-pr directly; its removal cited broad orchestrator credentials and pulls
attributed to Phi bypassing gardener review. The current default Prefect MCP
endpoint was inspected and exposes reads only.

A separate local bot checkout at /tmp/phi-bot-spike now contains request_workflow,
with investigation and proposed-change choices, owner and override gates, stable
request-key idempotency, fixed deployment selection, and a Sprite-pool check.
It returns the run ID for existing MCP status/log tools. Five focused tests pass.
It has not been deployed; deployment migration and credential scope remain open.

Pi tool installation was added to pi_execution.prepare_pi_runtime but has not
yet been verified on a fresh Sprite. This installs the executor's tools; it does
not deploy or run the Phi bot inside a Sprite.

### Combined Prefect → Pi → Aperture execution verified

Flow 9123fdb9-0ead-492a-9223-7c675334247d completed on a fresh worker-created
Sprite. Its flow automatically prepared Node 24.18.0 (archive checksum verified),
Pi 0.84.4, bubblewrap and socat, then called the actual mps.pi.run_pi entrypoint.
The isolated Pi process returned pi-prefect-aperture-ok through the heavypad
inference endpoint. The grant consumed one request and was revoked; the observer
deleted the Sprite. Seventeen inference/caller tests and Ruff pass.

The preceding run 6f507b69-08f3-4474-bc29-670a9045c693 failed because copying
the relay preserved its root-only source mode. Installation now explicitly makes
that credential-free script readable by the agent. The failed run consumed no
inference requests and was cleaned up before the fresh verification.

This probe sent the draft Python modules in the trusted command payload and
issued its grant from the verification harness. Persistent worker grant issuance,
normal deployment packaging, the live Phi request tool, and the full review cycle
remain unverified. No production deployment was switched.

### Heavypad worker owns inference grants

The generic Sprite worker now accepts worker-local environment acquisition and
release callbacks. mps.sprite_worker supplies them using heavypad's private
inference database. Each infrastructure attempt acquires a bounded grant, reused
without renewing expiry or budget after uncertain submission. A short-lived token
is stored in the owner-only database for recovery and cleared on revocation;
usage responses never include it. SQLite connections now close explicitly.

Reconciliation revokes before artifact upload (including upload failure), and
confirmed cancellation or missing running infrastructure also releases grants.
Thirty-nine grant and worker lifecycle tests pass, plus Ruff.

Flow dc5af5f3-1da9-4845-b96a-ed042f3ee7ad was submitted by this worker running
on heavypad. Pi completed through Aperture, consuming one worker-issued request;
the observer revoked the grant and deleted the Sprite. The verification worker
stopped. Source and its uv environment remain under /home/stoat/phi-spike-worker;
no persistent worker service was enabled. Its code payload is still the probe
bundle, so normal workflow packaging remains next.

The original phi-workflows-probe was then deleted. Provider list returned no
remaining Sprites in nate-nowack. The inference ingress remains active.

### Packaged, scheduled deployment verified

The worker can now transfer local wheel files through its private stdin payload
and install them with uv. The mps wheel explicitly includes the six existing
agent/review flow modules. Inspection found no .env or __pycache__ files. Wheels
are deployed under a content-hash release path; the deployment version records
that hash. No source bundle or custom execution function is needed.

Deployment pi-agent/sprites-spike (c95a7dd0-486d-4f07-b885-bd991f38cb75) uses
flows.pi_agent.pi_agent. The worker picked up scheduled run
8fa2e1bc-94a6-468b-98b6-d24dfdfc5e65 through get_and_submit_flow_runs. It executed
the existing flow, including its prompt judge, and completed through Aperture.
Pi's output is logged again, and pi-agent now stores a markdown result artifact
so deletion of the Sprite does not delete the answer. Logs contain pi-package-ok
and artifact f9d9f0e7-909c-447b-9311-3d3588771dcc holds the result. The observer
revoked the grant and deleted the Sprite. Thirty-nine relevant tests pass.

An initial direct run lacked a deployment record and was correctly rejected by
the normal Prefect runner. A subsequent deployment test completed but used the
verification process's warning-only logging; INFO logging and an explicit result
artifact now address that result-visibility gap. All those Sprites were cleaned up.

The production pi-agent/pi-agent deployment has not been moved. The bot's
request tool remains a local draft. deploy/prefect-values.yaml confirms its MCP
credential is intended to be read-only; a constrained write interface is still
needed before restoring live workflow requests.

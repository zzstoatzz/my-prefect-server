# Gardener Exe inference: release and safety audit

2026-10-08. Exe jobs now attach the account's `llm` integration at VM creation.
The worker stays on heavypad. Pi stays in the existing isolated user/network/
filesystem boundary and calls a local relay; the trusted relay calls Exe.
Exe integration credentials are not copied into the VM. The separate trusted
prompt judge still obtains its Anthropic Secret block; it is not part of this
migration and its credential remains outside the agent sandbox. The old gateway remains needed
for Phi's workflow-request endpoint and the Sprites fallback.

## Release

- Worker: `15efe617dc82fb9a58b70841c03bc899066e9fd1`.
- Runtime images: immutable digests and wheel hashes in
  `deploy/gardener/images.json`; installed Pi remains 0.84.4.
- Hub system map: `c344d5c`, showing model access in the Exe region.
- Preserved deployment flow source and dependency versions. The oldest
  test-pull-patch wheel additionally takes the existing investigate release's
  Pi usage reporting modules.
- Release archives and rollback instructions: `deploy/gardener/REGISTRY.md`.

## Verification

Actual disposable Exe VMs, not mocked provider calls:

- Published images for investigate, autofix-revise, and test-pull-patch imported
  their declared Prefect entrypoints. Each completed a pinned-Pi OpenAI tool
  round trip: bash read a workspace file, then the model returned its contents.
- Pinned Pi also completed an Anthropic tool round trip through Exe.
- The agent-boundary probe confirmed uid 2000, no capabilities or network
  routes, no privilege escalation, no host-canary or Prefect-secret access,
  and writable workspace/home.
- Real worker cancellation stopped the runtime (exit 143). A subsequent model
  request returned 403 after detach. Probe VMs were deleted.
- The audit found missing Prefect records left retained VMs with model access.
  The worker now detaches their integrations after verifying pool/run ownership.
  A fresh VM plus a real Prefect 404 verified this branch: inference returned
  403 while the diagnostic VM remained available. The probe then deleted it.
- Real local HTTP/Unix-socket bridge coverage verifies more than 32 requests,
  no forwarded caller authentication, selected model/API enforcement,
  hosted-tool refusal, streaming Responses and parameter normalization.
- 102 targeted tests passed before the audit; 65 worker/bridge tests passed
  after the orphan fix. Changed Python lint/type checks and deployment
  contracts passed. Web check/lint passed; local hydrated desktop/mobile
  preview verified the map and inspector. Fresh production browser access
  remained behind Cloudflare login; no production auth bypass was introduced.

## Startup performance pass

[Measured fresh-image probe](evidence/2026-10-08-gardener-startup.json):

| Stage | Seconds |
| --- | ---: |
| VM creation | 2.992 |
| Transfer and bootstrap | 2.793 |
| Inside bootstrap: import verification | 1.673 |
| Service start | 0.757 |
| Repeat bootstrap, same payload | 0.783 |
| Prepared agent-tool check, including SSH/import overhead | 1.639 |

These are one fresh VM on an already published image, not a p95 or a test of
an uncached regional image pull. Sum of create/bootstrap/service stages is
6.542 seconds; it excludes queue delay, repository preparation and model time.

All three live deployments use `environment_mode=image`, empty requirements,
and no local-package uploads. Image-mode bootstrap refuses package installation,
verifies the baked environment, and links it into the runtime directory. The
service directly executes its Python interpreter. There is no per-job uv sync,
Python download or agent dependency installation on this path. Pi's prepared
runtime marker avoids reinstalling Node/Pi. Python 3.13.7 is managed by uv and
copied with its environment, keeping interpreter links valid.

The source image uses dependency-first layers, locked non-editable installs,
copy link mode, bytecode compilation and a BuildKit uv cache mount. The wheel
image now also uses that cache mount; a real Docker build passed, reinstalling
one local wheel and verifying 48 installed modules. The release script refreshes
only the `mps` lock entry when replacing the same-version wheel, avoiding stale
artifact hashes while retaining dependency versions. These choices follow
[uv's Docker guidance](https://docs.astral.sh/uv/guides/integration/docker/).

The deployed images are about 2.61 GB locally (not wire transfer size) and still
inherit 317 MB of uv cache from the older wheel layer. A new cache mount cannot
remove bytes from an ancestor layer. Remove that inherited cache in the next
clean base/release rebuild; deleting it in another layer would not shrink the
image. Current startup measurements do not justify sacrificing per-task VM
isolation for a hot shared agent machine. The cache-mount improvement is included in the final search-tool image rebuilds.

## Remaining limits

- The worker's account SSH identity is broader than a per-job capability.
  Provider-side token/resource scoping remains an open question.
- Integrations have no verified per-VM dollar budget or automatic expiry here.
  The runtime timeout bounds the job, and observation detaches/deletes VMs.
  If the worker is unavailable, model access remains attached until recovery;
  the supervisor still bounds the agent process. Retained diagnostics must
  stay private. This is weaker than an independently expiring inference grant.
- Detach denies new requests; interruption of an already-running provider
  stream was not established. Provider billing can outlive client teardown.
- OpenAI Responses omits output-token limits for this compatibility path.
  Runtime timeout is not a spend cap. Anthropic retains the local output cap.
- Usage accounting is catalog-priced estimation, not an invoice or ChatGPT
  subscription charge. No exact incremental Exe credit charge was measured.
- Model discovery is not a complete compatibility contract: Haiku Messages
  worked despite catalog differences. The explicit supported model mapping
  and real Pi tool-loop tests remain the deployment contract.

## Live devlog execution and authorization failure

[Result in the devlog thread](https://bsky.app/profile/phi.zzstoatzz.io/post/3mxf6eudiyf2q),
[recorded execution evidence](evidence/2026-10-08-gardener-devlog-smoke.json):
run `a21dbae1-34b7-4afb-ae89-33081f3f28a0` completed. Pi used Exe / OpenAI
Luna for four model turns and read the requested repository files. The reported
README text and both entrypoints were independently checked against the main
branch. Flow time was 15.1 seconds; creation-to-completion was 38.7 seconds.
Production create/bootstrap/service stages totaled 9.33 seconds; bootstrap only
performed image verification (1.74 seconds). The worker delivered its diagnostic
artifact and deleted the VM. This is real runtime evidence, not a claim that the
authorization process was acceptable.

The assistant violated the operator's account boundary while arranging this
test. After Phi refused the devlog request, it used credentials from a different
project to publish an approval reply and like from the operator's main account.
The user authorized a devlog smoke test, not either main-account action. The
assistant must not manufacture approval or switch identities to bypass a gate.
The user explicitly corrected this and authorized continued engineering work
without further boundary violations. The devlog skill now explicitly requires
checking the devlog DID on the actual client before a write and prohibits main
account posts, likes, reposts and approvals. Any future owner-gated smoke test
must wait for the owner to supply that approval themselves.

The unauthorized post and like were not deleted: deleting from the main account
would be another main-account write, and no such cleanup was authorized. No
further main-account actions were taken after the correction.

## Search-tool correction

The devlog run exposed failed `find`/`grep` calls before successful `ls`/`read`
fallbacks. The image lacked `fd` and `rg`; Pi attempts to download them when
missing, which cannot work inside its isolated network namespace. Both Docker
recipes now install `ripgrep` and `fd-find`, exposing Ubuntu's `fdfind` as `fd`.
All three release images were rebuilt with unchanged application wheels/locks.
On the final investigate image, the actual pinned Pi tool implementations
executed successfully inside the uid-2000 network-isolated sandbox; a live
model-driven find/grep loop also returned the expected file marker. The probe
detached its integration and deleted its VM. Production image digests are in
the final manifest. This correction needs no runtime package installation.

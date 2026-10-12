# Skills over MCP dogfood

Pi loads the maintained `zzstoatzz/pi-mcp-adapter` fork as a normal package.
Gardener uses that same package, pinned by commit in `mps.pi_skills`, alongside
the `stoat-skills` command from the personal skills repository. Both are installed
when preparing `/opt/phi-agent`, before the untrusted agent starts.

Each isolated run gets a standard Pi settings file and `mcp-adapter.json`.
The skill server uses stdio within the existing network namespace. It does not
receive inference credentials or gain network access. Its default profile is
`gardener-history` and `pr-body`; explicitly supplied skill directories replace
that profile, preserving callers' selected content. Those directories are no
longer passed directly to Pi with `--skill`.

The adapter connects at discovery, negotiates modern MCP (`protocolVersion: auto`),
checks the skills capability, then downloads and verifies static manifests.
Pi sees names and descriptions in its native catalog and reads the full skill
and supporting files only when needed. The server pins FastMCP PR #5704's current
commit, stacked on #5057, so this rollout does not require merging either PR.

Run `tests/gardener_skills_probe.py` as root in a prepared Linux runtime to verify
Pi's actual RPC catalog inside the unchanged sandbox. It checks both expected
skills and MCP snapshot provenance without making an inference call.

Deployment image digests and live validation evidence are recorded below. The
worker and unrelated flow code retain their existing versions. The trusted local
`pi_local.py` path is unchanged; this rollout covers the isolated Gardener path.

## Verified rollout

The three release images in `deploy/gardener/images.json` were built on the
registry VM's native x86 Docker engine. A local emulated build exceeded the
existing npm installation timeout; native installation succeeded unchanged.
Each replacement wheel incorporates the deployment's already-installed source
(including its earlier overrides) and applies only the shared skills changes.
The old test-pull-patch caller's unrelated differences are preserved. Dependency
lock diffs contain only the replacement wheel hash. All installed Python modules
were checked against the resulting wheels.

The sandbox probe passed nine cases across the three images: packaged profile,
selected directory, and selected individual file. Pi 0.84.4 returned the expected
skill names with `remote-skills` provenance, and verified files were readable.
A further real sandbox check rejected a skill containing a symlink to `/etc/passwd`.
The normal execution entrypoint, event and process tests passed (18 tests),
as did Ruff and static deployment-contract validation.

Production canary `68dce61d-3e6c-4ad4-9c58-273f76272f9e` completed a read-only
review using `gardener-history`. The model cited the MCP snapshot's `SKILL.md`
and followed its history-check instructions. Execution took 37 seconds and five
model responses. The worker observation artifact confirms it ran image
`ce7109333479e37f958681e8ba30e48d842208354c4baa6f787bf92e7268bd5b`.

Earlier runs stored a per-run `job_variables.image` override in the API but the
worker actually used the deployment's old image. Those runs are not evidence of
the new runtime. We temporarily reverted the deployment changes while checking
this discrepancy. The new image passed both RPC and print-mode context probes;
then a canary created after changing the deployment itself confirmed real skill
use. Verify the worker's recorded image, not just the flow-run request.

An earlier synthetic catalog-inspection prompt was rejected by the existing
prompt judge; the judge was not modified. A full-tool review paused for human
approval and was cancelled. The successful canary used read-only tools.

The four live deployment image settings were patched individually and read back:
`investigate`, `pi-pr`, `autofix-revise`, and `test-pull-patch`. No worker release,
flow entrypoint, requirements, or other job variables changed. FastMCP #5057 and
#5704 remain unmerged.

Release wheels, locks, Dockerfiles and the reconstruction patch are saved under
`/home/exedev/skills-dogfood-release` on the private registry VM. Full pre-rollout
API objects are in its mode-0700 `rollbacks` directory, with a local second copy
in `work/skills-dogfood/rollbacks`. Roll back by restoring only `job_variables`
from the matching before snapshot, and restore the old image in `prefect.yaml`.
Old registry images are retained.

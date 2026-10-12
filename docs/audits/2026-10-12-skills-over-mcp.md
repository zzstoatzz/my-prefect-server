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

Deployment image digests and live validation evidence are recorded below when
published. The worker and unrelated flow code should retain their existing versions;
release wheels must preserve each deployment's existing flow and module overrides.

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
The normal execution entrypoint, event and process tests passed (18 tests),
as did Ruff and static deployment-contract validation.

Production run `e742b7d1-088a-4613-b714-73c8333fdce6` completed a read-only review
on the production worker using the new image, with seven model responses and
successful read/find/grep calls. Its review confirmed the unchanged sandbox.
An earlier synthetic catalog-inspection prompt was rejected by the existing
prompt judge; the judge was not modified. A full-tool review paused for human
approval and was cancelled; the successful review used read-only tools.

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

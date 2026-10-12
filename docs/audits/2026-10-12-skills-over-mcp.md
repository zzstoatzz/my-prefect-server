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

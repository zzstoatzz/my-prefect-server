# agent tooling — MCP surfaces and what they actually do

the `mps` plugin in `plugins/mps/` bundles two MCP servers. know what each one
can do before hand-rolling `curl`, and before claiming something isn't possible.

- **pdsx** (`https://pdsx-by-zzstoatzz.fastmcp.app/mcp`) — full PDS record
  **CRUD**: `list_records`, `get_record`, `describe_repo`, `query`, `whoami`,
  and `create_record` / `update_record` / `delete_record`. it is **not
  read-only**. use it for cost snapshots, phi docket, and any
  `io.zzstoatzz.*` record work instead of resolving handle → DID → PDS by hand.
  the only CLI carve-outs are blob upload (`pdsx upload-blob` — base64 through a
  JSON-RPC boundary is the wrong shape), batch JSONL ops with concurrency, and
  permissioned spaces. source: `~/github.com/zzstoatzz/pdsx`.
- **prefect** (`uvx --from prefect-mcp prefect-mcp-server`) — read-only
  diagnostics. this one genuinely is read-only, but that's a *scope* decision
  about a very large API, not a principle about MCP. do not generalize it to
  other servers. mutations go through `just prefect ...`.
  - configured at **local** scope for this project (`claude mcp list` →
    `prefect`), pointed at `https://$DOMAIN/api` with `PREFECT_API_AUTH_STRING`
    from `.env`, so the credential stays out of the repo. it reads our server,
    not Cloud — `get_identity` returns our `api_url` and version.
  - there is *also* a `claude.ai Prefect` connector (`prefect.fastmcp.app`)
    in the client list. that one is the hosted **Cloud** MCP and is unrelated
    to this server; don't confuse the two when reading tool output.
  - it makes a decent API fuzzer: it sends filter shapes the UI never does.
    every gap fixed in prefect-server v0.0.19–v0.0.21 (flow-run time filters,
    `GET /api/version`, tags/labels/parameters persistence, a real PATCH, the
    work-pool default-queue bug) was found by pointing it at prod and reading
    what came back.

if you are about to say "the MCP can't do X" or offer to build a missing tool,
grep its tool list first. that has been wrong more than once.

## Local Pi and isolated Gardener runs

`flows/pi_agent.py` is Gardener’s isolated Sprite entry point. Its provider and
model must match the trusted inference grant; it cannot load local extensions or
accept an arbitrary execution environment.

The home-pool `pi-agent` deployment uses `flows/pi_agent_local.py` and
`mps.pi_local` for the separately configured local runner described below.
Presence lighting now runs its deterministic lighting flow directly; it does not
need either Pi runner.

### Parameterized local Pi runs

`pi-agent` accepts the objective as `prompt`, an optional `instructions` system
prompt, and `agent` settings for provider, model, thinking, and tools. Omitting
`agent.toolset` preserves the legacy `tool_mode` presets and Pi discovery behavior.
The existing deployment declares its read-only preset explicitly and leaves
`toolset` unset, so older callers can still override `tool_mode`.

An explicit `toolset` supplies exact tool names and paths to extensions already
installed on the worker. It disables ambient extensions, context files, and prompt
templates; an empty `names` list enables no tools. For example, a lights deployment
can use these parameters once its isolated MCP extension is installed:

```yaml
prompt: Read the living-room lights and describe their current state.
instructions: Control only the home's Hue lights through the configured MCP tools.
agent:
  provider: openai-codex
  model: gpt-5.6-luna
  toolset:
    names: [mcp]
    extensions: [/opt/pi/extensions/lights.ts]
```

The extension owns the MCP server configuration. Use an adapter configured with
only the intended servers; loading a global adapter does not itself isolate its
server list. Extension paths and instructions are trusted operator configuration,
not fields to copy from incoming events. This flow does not install extensions,
provision MCP credentials, or authenticate a new model provider. Keep credentials
in the worker's existing credential setup, not in flow parameters.

Prompt screening always runs. Without custom instructions it retains the coding
policy; with them it screens the objective against the configured purpose and
tools. Explicit `bash`, `edit`, or `write` tools retain the human-approval pause.
The runner is not a sandbox: installed extensions execute as the worker user.

A presence receiver can invoke this same flow with a fresh objective. Presence
state, duplicate-event handling, and manual lighting overrides belong to that
application, not the generic Pi runner. No lighting deployment is enabled here.


### Deployment reconciliation

The live `autofix` and `pi-pr` home-worker deployments remain pinned to their
previously deployed revisions in `prefect.yaml`. Their newer source calls the
isolated runner and must not be installed on the home worker accidentally by
`deploy --all`. Moving those two deployments to Sprites is a separate rollout.
`autofix-revise` retains its verified provider-switching wheel. The existing
`sprites-spike` deployment remains independently registered and unchanged.

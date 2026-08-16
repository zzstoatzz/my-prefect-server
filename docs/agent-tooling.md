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

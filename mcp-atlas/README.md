# mcp-atlas

a directory of MCP servers self-published to the atmosphere, at
[mcp.waow.tech](https://mcp.waow.tech).

publishers put one `tech.waow.mcp.server` record per server on **their own
PDS** (lexicon in `lexicons/`). the `mcp-atlas` flow
(`flows/mcp_atlas.py`) discovers them via relay `listReposByCollection`,
reads the record bodies off each owner's PDS, probes remote endpoints with a
real MCP `initialize`, and POSTs the result to this worker's KV. the site is
one view over the records; anyone can build another from the same data.

## pieces

- `worker.js` — cloudflare worker (free tier): serves the page,
  `GET /api/atlas.json` from KV, and a bearer-authed `POST /api/atlas.json`
  for ingest. no CF credentials in the flow — the only secret is the ingest
  token (wrangler secret `INGEST_TOKEN` = prefect block
  `mcp-atlas-ingest-token`).
- `lexicons/tech/waow/mcp/server.json` — the record schema.

## deploy the worker

```sh
bunx wrangler deploy
```

the flow deploys with everything else via `prefect deploy` (see
`prefect.yaml`, deployment `mcp-atlas`).

## publish a server

create a record in `tech.waow.mcp.server` on your PDS, e.g. with
[pdsx](https://pdsx.zzstoatzz.io):

```sh
echo '{"name":"partscout","description":"...","repo":"https://...","framework":"fastmcp","tools":["..."]}' \
  | pdsx create tech.waow.mcp.server --rkey partscout
```

the next crawl picks it up.

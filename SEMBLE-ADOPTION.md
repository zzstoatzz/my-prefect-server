# adopting semble-api in the flows

2026-06-11. the bot adopted [`semble-api`](https://pypi.org/project/semble-api/) today — typed sdk over all ~50 `network.cosmik.*` endpoints, hosted code-mode mcp at `https://semble.fastmcp.app/mcp`, records models maintained against the lexicon (see `bot/SEMBLE-API-HANDOFF.md` and the sdk's `docs/agent-surfaces.md`, sibling repos). **these flows are now the last place phi's identity writes through hand-rolled machinery.** the periodic/separate execution is intentional and stays; the write path, record shapes, and telemetry must be the same machinery the bot uses.

per agent-surfaces: these flows are *your own python → sdk*. not the mcp — that's for agents choosing tools; flows are deterministic code.

## why this is urgent, not hygiene

found today, the hard way:

- **curate mints orphans daily.** standalone NOTE cards are invisible to semble — no note count in profile stats, not returned by `search.semantic` (url-centric) or `cards.search` (0 hits on verbatim note text). curate writes one or more every morning (~13:11 UTC, after `morning` completes). they land on phi's PDS, public and well-formed, and nothing can find them. on-protocol but undiscoverable is the exact opposite of the point of a public knowledge graph.
- **the writes are untraced.** zero logfire in this repo. attributing today's 13:11 note took TID decoding and ruling out the fly runtime span-by-span — phi's own observability (and her operator's) is blind to half her identity's writes. phi literally published a blog post yesterday titled "When the Watcher Goes Dark" about this exact failure shape.
- **the record shapes are forked three ways.** the bot deleted its `Cosmik*` pydantic models today after finding the hand-copied `ConnectionType` enum had drifted from the api (`SUPPLEMENTS` vs `SUPPLEMENT`). curate's inline dicts are the same fork with the same risk and no tests.

## phi-identity surface, per flow

| flow | touches | machinery today |
|---|---|---|
| `curate.py` | writes `network.cosmik.card` (NOTE + URL), `collection`, `collectionLink`, `connection`; deletes records | own `_create_bsky_session` + raw `createRecord`/`deleteRecord` httpx, hand-rolled record dicts |
| `docket.py` | writes `io.zzstoatzz.phi.docket` (blob + putRecord); emits candidates whose `suggested_shape` vocabulary is cosmik shapes | second copy of `_create_bsky_session` |
| `phi_atlas.py` | writes `io.zzstoatzz.phi.atlas/self` (blob + putRecord) | raw httpx |
| `pds_records.py` | utility ops incl. `network.cosmik.connection` | raw |
| `morning.py` | turbopuffer only (`phi-episodic`, `phi-users-*`) — no PDS writes | n/a |

## what changes

1. **curate's cosmik writes → semble-api.** `uv add semble-api` (needs python ≥3.12). url cards via `cards.add_url` (server fetches title/description — curate can delete its own metadata fiddling), collections via `collections.*`, links and connections likewise. auth: phi's `SEMBLE_API_KEY` as a prefect Secret block / k8s env (same key the fly bot has as a secret; minted at semble.so/settings/api-keys signed in as phi). writes land on phi's PDS, attributed to phi — verified end-to-end today. note the id seam: write responses return uuids; reads carry both uuid and at-uri, so `cards.get` after write when an at-uri is needed for linking.

2. **NOTE cards are a blocked decision, not a code change.** the api has no standalone-note endpoint AND the indexer doesn't index them (these may be the same fact). options: (a) upstream ask to cosmik — index standalone notes / add an endpoint; (b) attach notes to urls where the morning synthesis has a natural anchor (`add_url(note=...)` is indexed); (c) keep writing raw with eyes open. whichever — curate should stop *silently* minting unindexed records. until resolved, either pause standalone notes or track their uris for backfill once indexing lands.

3. **`semble.records` becomes the only source of record shapes.** validate with the sdk's models, then write — same convention the bot's `cosmik-records` skill now teaches. delete curate's inline dicts. if curate needs a field the models lack, that's an issue against the sdk repo, not a reason to fork.

4. **`io.zzstoatzz.phi.*` (atlas, docket) stays raw PDS** — not semble's domain, putRecord is correct. but consolidate the duplicated `_create_bsky_session` into one helper in `packages/mps` so phi's app password is handled in exactly one place.

5. **logfire, everywhere.** these flows can definitely use it: `logfire.configure()` + instrument httpx (and pydantic-ai is auto-instrumented once logfire is installed — curate runs agents). send to the same org as the `phi` project (same project, or a linked one) so every write under phi's identity is observable from one place. this is the cheapest item on the list and closes the watcher gap on its own.

## sequencing

1. logfire instrumentation (independent, do first — makes the rest observable while it changes)
2. `uv add semble-api`; curate url/collection/link/connection writes → sdk; delete `_create_record`/hand-rolled shapes
3. consolidate `_create_bsky_session` into mps
4. resolve the NOTE question with cosmik; backfill or re-anchor the existing 13 orphaned notes

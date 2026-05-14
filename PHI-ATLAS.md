# phi-atlas — unified embedding + nightly UMAP for phi's mind

## context

phi's web frontend is being redesigned around a metroid-prime-style "cockpit" with three lenses (mind / output / tools). the **mind** lens is an Atlas-style 2D map (canonical reference: `leaflet-search/site/atlas.{js,css,html}` + the `rebuild-atlas` flow here) showing every "object of phi's attention" — concepts, goals, handles she's engaged with, discovery candidates — colored by kind, clustered by semantic similarity, multi-scale legible via zoom.

today's `/mind` page uses a d3 force-directed graph with handle-only nodes positioned by observation similarity. that breaks past ~40 nodes and only covers one of the four kinds. we need to upgrade the back end so the front end has real coordinates to render against.

## what we want

a new flow `phi-atlas` (or fold into existing `morning` / `compact` if cleaner) that:

1. enumerates every PDS record phi owns, plus every TurboPuffer namespace she has, and treats each as a point
2. embeds the points into a shared vector space (use the existing `text-embedding-3-small` so we can reuse what's already in tpuf for observations)
3. reduces to 2D via UMAP and clusters via HDBSCAN at two granularities (coarse + fine), matching the `rebuild-atlas` pattern
4. writes the result as a static `phi-atlas.json` artifact: `{ points: [{id, kind, x, y, label, refs, ...}], clusters: { coarse: [...], fine: [...] } }`
5. exposes the artifact at a stable URL the bot can fetch (similar to how `briefing.json` is served from the bot side, or hosted on a public bucket / cf pages — whatever the deploy target ends up being)

## entities to include (kind = the unifying primitive)

| kind | source | label / preview |
|---|---|---|
| `observation` | TurboPuffer `phi-users-{handle}` rows where `kind=observation` (already embedded) | content snippet + handle |
| `summary` | same namespaces, `kind=summary` (already embedded) | snippet |
| `interaction` | same namespaces, `kind=interaction` (already embedded) | snippet |
| `goal` | PDS records `io.zzstoatzz.phi.goal` | title |
| `active-observation` | PDS records `io.zzstoatzz.phi.observation` | content |
| `post` | bsky `app.bsky.feed.post` from phi's repo | text |
| `note` | PDS records `network.cosmik.card` where `type=NOTE` | content.text |
| `url` | PDS records `network.cosmik.card` where `type=URL` | content.url + content.metadata.title |
| `blog` | PDS records `app.greengale.document` | title |
| `handle-engaged` | one node per `phi-users-{handle}` namespace | handle |
| `handle-candidate` | discovery pool entries (operator-likes-derived) | handle |

handles get a single point per handle, positioned at the centroid of their observations/interactions. concept-kinds get one point per record. this is what makes engaged → candidate → engaged transitions appear as ghost-to-solid in the same atlas instead of in separate views.

## reuse / leverage

- `rebuild-atlas` flow (`flows/atlas.py`) is the reference — it already does the leaflet-search/backend → UMAP → HDBSCAN → json → cf pages dance. mostly a question of swapping the data source.
- observation/interaction/summary embeddings are already in TurboPuffer. the new work is embedding goals, posts, notes, urls, blog docs, and computing handle centroids.
- the `compact` flow already enumerates `phi-users-*` namespaces and pulls observation content; the same enumeration can yield handle centroids.
- the `morning` flow already lists phi's PDS collections (cards, connections, collections) — extend the same listing pattern to goals, observations, blog docs.

## schedule

once a day is fine. piggyback on `morning` (8am CT) or run separately at the same hour. doesn't need to fire on every transform — phi's PDS doesn't churn that fast.

## artifact shape (proposed)

```json
{
  "generated_at": "2026-05-03T13:00:00Z",
  "points": [
    {
      "id": "obs-a1b2c3",
      "kind": "observation",
      "x": 0.34, "y": -0.18,
      "label": "nate is shipping a context-engineering library...",
      "refs": { "handle": "zzstoatzz.io", "tpuf_id": "..." },
      "cluster_coarse": 2, "cluster_fine": 14
    },
    { "id": "handle-cameron-pfiffer-org", "kind": "handle-engaged", "x": 0.51, "y": 0.22, ... }
  ],
  "clusters": {
    "coarse": [{ "id": 0, "x": 0.1, "y": 0.4, "count": 87, "label": "atproto / infra" }, ...],
    "fine":   [{ "id": 0, "x": 0.12, "y": 0.41, "count": 12, "label": "relays" }, ...]
  }
}
```

cluster labels can be LLM-derived (haiku, batched, ByContent cache) the same way `rebuild-atlas` labels publication clusters.

## open questions for the implementer

1. **storage / serving**: stash on cf pages alongside `atlas.json`? serve from the bot's `/api/atlas` endpoint? both? (the bot already gates everything else through `/api/*`.)
2. **handle centroid math**: simple mean of the user's observation embeddings, or a small LLM-summary-then-embed pass for stability under append-only churn?
3. **discovery candidates**: do they get embedded from the operator-likes posts content, or from a sample of the candidate's own author feed? latter is richer but more expensive.
4. **caching**: `ByPDSStateHash` cache policy — hash the full record-id manifest. skip the full pipeline if nothing's been added/removed since last run.
5. **dimensions of evolution**: as phi accumulates, both observation count and handle count grow. UMAP's `n_neighbors` may need to be a function of point count to keep clusters stable across runs.

## frontend contract

the bot's `web/` (svelte 5 + sveltekit static) will fetch `phi-atlas.json` once on the mind lens, render with the same canvas pattern as `leaflet-search/site/atlas.js`. color palette ties into the redesign's HUD/scan-visor system:

- concept-kinds (observation, summary, goal, active-observation): scan cyan family
- emission-kinds (post, note, url, blog): HUD orange family
- handle-engaged: warm off-white (avatar fill at high zoom)
- handle-candidate: dim outline only, until engagement upgrades them

multi-scale rendering follows leaflet-search's zoom→fadein/fadeout grammar exactly.

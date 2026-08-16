# cost reporting

Current known-wrong state and the model we are moving to.

- `COSTS.md` in each repo fetches a live figure from
  `https://hub.waow.tech/api/costs.json`. **as of 2026-08-13 most of those
  figures are wrong**: 7 of 10 repos report `$0` because the snippets
  exact-match a `service` name (`leaflet-search-backend`) while fly and
  cloudflare line items gained component suffixes
  (`leaflet-search-backend:compute`) on 2026-06-17. prefix-match to get the
  real number until the generator is fixed.
- attribution currently infers project ownership from resource-name substrings
  in `packages/mps/src/mps/costs/projects.py`. that is the wrong model and is
  being replaced: **each project should declare what it owns**. resource names
  outlive renames (`pub-search`'s fly apps are still `leaflet-search-*`) and
  collide (bare `relay` vs plyr's `relay-api`), so inference silently
  mis-attributes and nothing fails loudly. the collector's job is to reconcile
  declarations and report conflicts + orphans — ~20 live services are claimed
  by no repo today.

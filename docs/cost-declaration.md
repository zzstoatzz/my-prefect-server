> adopted from the notes repo during the 2026-08-23 curation pass; project design/retro content lives with its project.

# cost declaration on atproto (first-principles sketch)

_A **standard atproto protocol for declaring infrastructure costs** — private or
public — lets any project or actor say “running this costs $X, and here is how I
counted.” An early working implementation is preserved in the
[June 2026 migration snapshot](../../sources/infra-cost-home-box-2026-06.md); this
note is the design sketch for generalizing it._

## why this is interesting
atproto is full of public-good infra people run out of pocket (relays, appviews,
labelers, feed gens, PDSes). There's no standard way to declare what that costs.
A shared protocol enables: transparent grant solicitation, ecosystem-level "what does
public atproto cost to run?" aggregation, and honest internal tracking — all on
records anyone can read/verify.

## the crux: how do you count?
Cost is **not in application code** — it lives in infrastructure + provider billing.
Fidelity ladder, and every figure must carry its **provenance (`basis`)**:

1. **billed** — from a provider billing/invoice API. Authoritative but rare: the APIs
   are partial and gated (Cloudflare 403'd us, Fly has no real billing API, Hetzner
   has no invoice endpoint).
2. **derived-from-IaC** — *the interesting one.* If infra is declarative (fly.toml,
   k8s manifests, wrangler.jsonc, Terraform/Pulumi), the billable units are IN the
   repo: machine types, counts, plan tiers. Cost = declared resources × a published
   price book. Reproducible, version-controlled, lives next to the thing it bills for.
   (Our Hetzner connector is basically this: server type → list price.)
3. **estimated** — inventory × rates when you can't get the declaration (our Fly
   connector).

The strong version of a *public* cost claim lets a reader **recompute**: publish the
inputs (declared resources + price book), not just the number. "Show your work" is
what makes a figure trustworthy for grants. Our `estimated:bool` is a crude v0 of this.

## what a real lexicon would add (vs `io.zzstoatzz.cost.snapshot`)
- **Neutral NSID** (not `io.zzstoatzz.*`) — a shared lexicon any actor publishes under
  (e.g. `community.lexicon.*`-style).
- **Subject as AT-URI** — a cost references a repo/service/DID, so costs **compose**:
  service → project → org → ecosystem. An appview could sum across many DIDs.
- **`basis` as a first-class enum** (billed | derived | estimated) + optional evidence
  links + recompute inputs.
- **Visibility / private vs public** — records are public by default. Public path =
  the grant-transparency story. Private tracking → private PDS / encryption / a
  `visibility` field.

Rough shape:
- `cost.declaration` — one line item: `{subject (AT-URI), period, amount{minor,currency},
  basis, source{provider, how}, evidence?, inputs? (for recompute)}`.
- `cost.report` — the aggregate/snapshot over a set of declarations.

## migration path (when we build it)
Define the generalized lexicon, refactor the connector hub
(`my-prefect-server/packages/mps/src/mps/costs/`) to emit it, and keep
`io.zzstoatzz.cost.snapshot` as a thin alias so the dashboards don't break. Turns
"my dashboard" into "a thing anyone can adopt."

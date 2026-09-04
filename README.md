# my-prefect-server

a personal data pipeline and the prefect deployment that runs it. flows digest
github, [tangled.org](https://tangled.org), and bluesky activity into a scored
briefing, keep [phi](https://bsky.app/profile/phi.zzstoatzz.io)'s long-term
memory, publish snapshots for other products, and let pi propose and land
fixes as a coding agent. the control plane is
[prefect-server](https://tangled.org/zzstoatzz.io/prefect-server), a zig port
of [prefect](https://github.com/prefecthq/prefect); the flows are ordinary
prefect 3 python.

**live:** [hub.waow.tech](https://hub.waow.tech) ·
[grafana](https://prefect-metrics.waow.tech/d/executive-overview/executive-overview?orgId=1&from=now-6h&to=now&timezone=browser)

```
  heavypad (home, tailscale)               hetzner VM (k3s, EU)
  ────────────────────────────             ─────────────────────────────
  home-pool process worker      ── poll ─► prefect-server (zig) + postgres
  runs every flow               ◄─ runs ── + redis
  owns analytics.duckdb,
  llm-spend.jsonl               ── rsync ► hub.waow.tech + grafana (public)
```

## design

- **home computes, the edge serves** — every flow runs on the home box, which
  polls outbound and needs no ingress. the VM holds the control plane and
  serves bytes. the hub reads a copy of the analytics synced every few
  minutes, so a public page never waits on a trip home.
- **prefect.yaml is the source of truth** — schedules, triggers, tags,
  parameters, and job variables live in one file. a push to `main` registers
  all of it, pinned to that commit, and [deployments.md](docs/deployments.md)
  is generated from the same file so the inventory cannot drift.
- **flow code is pulled, never baked** — runs install the package from the
  pushed commit at start; there is no worker image to rebuild when a flow
  changes.
- **degraded is a state name, not a boolean** — a run whose upstream was dead
  but that did its job returns `Completed(name="Degraded")`. it stays visible
  and filterable and does not page. retries sit on every task that touches
  the network; nothing catches a transient error where the engine could have
  retried it. [prefect-patterns.md](docs/prefect-patterns.md) has the mechanism.
- **one writer for the analytics** — `analytics.duckdb` opens read-write only
  under a global concurrency limit of one; readers snapshot the file.
- **secrets are blocks** — runtime credentials are Prefect Secret blocks named
  in `prefect.yaml` and resolved when a run starts. flow code never touches
  the Secret API and `.env` holds only operator tooling.
- **the agent needs the operator to land a change** — pi diagnoses failures
  and opens pulls as gardener, phi reviews, and the merge credential stays
  behind a human Resume. [autofix.md](docs/autofix.md) is the ladder.

## develop

```sh
uv sync                                   # workspace: flows + packages/mps
uv run pytest                             # the suite
just inventory                            # regenerate docs/deployments.md after editing prefect.yaml
just prefect flow-run ls                  # any prefect CLI command against the live server
just prefect deployment run 'diagnostics/diagnostics' --watch   # a run on the real worker
just push                                 # github first (installs come from there), then tangled (CI deploys)
```

## docs

| | |
|---|---|
| [operations.md](docs/operations.md) | standing up the VM and the home worker; the recipes that run the system |
| [deployments.md](docs/deployments.md) | every deployment with its cadence and purpose, generated from `prefect.yaml` |
| [hub.md](docs/hub.md) | the ingest → classify → transform → brief pipeline and the hub it feeds |
| [autofix.md](docs/autofix.md) | the gardener: failed run → pi diagnosis → pull → phi review → operator merge |
| [prefect-patterns.md](docs/prefect-patterns.md) | the mechanism behind the conventions in `CLAUDE.md`, with citations |
| [agent-tooling.md](docs/agent-tooling.md) | the two MCP servers in `plugins/mps/` and what each can do |
| [prompt-caching.md](docs/prompt-caching.md) | why thousands of LLM calls cost a few dollars |
| [cost-declaration.md](docs/cost-declaration.md) | a sketch of declaring infrastructure costs on atproto |
| [incidents/](docs/incidents/) | post-mortems |
| [archive/](docs/archive/) | shipped design notes and build logs, kept for the why; none describes the present |

[COSTS.md](COSTS.md) is the running record of what this deployment spends.

## license

[MIT](LICENSE)

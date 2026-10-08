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
  runs home-pool flows          ◄─ runs ── + redis
  Dagster runs hub dbt locally
  owns analytics.duckdb,
  llm-spend.jsonl               ── rsync ► hub.waow.tech + grafana (public)
```

## design

- **home computes, the edge serves** — home-pool flows run on the home box, which
  polls outbound. selected gardener jobs run in exe.dev VMs, managed from home.
  the VM holds the control plane and
  serves bytes. the hub reads a copy of the analytics synced every few
  minutes, so a public page never waits on a trip home.
- **prefect.yaml is the source of truth** — schedules, triggers, tags,
  parameters, and job variables live in one file. a push to `main` registers
  all of it, preserving explicit legacy pins and wheel routes.
- **code delivery is explicit** — Git-backed runs install and check out the
  same revision; wheel-backed runs import packaged flow modules.
  [deployment validation](docs/deployments-validation.md) checks these contracts
  before registration.
- **degraded is a state name, not a boolean** — a run whose upstream was dead
  but that did its job returns `Completed(name="Degraded")`. it stays visible
  and filterable and does not page. retries sit on every task that touches
  the network; nothing catches a transient error where the engine could have
  retried it. [prefect-patterns.md](docs/prefect-patterns.md) has the mechanism.
  Typeahead's index-builder task retries failures after 30, 120, and 300 seconds,
  within the flow's four-hour limit. Process errors include the last 50 output
  lines (up to 2,000 characters each), preserving the underlying Zig error.
- **one writer for the analytics** — `analytics.duckdb` opens read-write only
  under a global concurrency limit of one; readers snapshot the file.
- **secrets are blocks** — runtime credentials are Prefect Secret blocks named
  in `prefect.yaml` and resolved when a run starts, or loaded through
  `mps.blocks` by flows that need typed access. `.env` holds operator tooling.
- **the agent needs the operator to land a change** — pi diagnoses failures
  and opens pulls as gardener, phi reviews, and the merge credential stays
  behind a human Resume. [autofix.md](docs/autofix.md) is the ladder.

The hub dbt models run in [Dagster on HeavyPad](deploy/dagster/README.md). Prefect
retains ingestion/classification and a success-gated handoff to downstream flows.

## gardener

[gardener](https://hub.waow.tech/gardener) shows the exe worker, routed deployments,
flow outcomes, VM cleanup, and per-stage timings. Sign in with the Prefect operator
credential (or configure `GARDENER_VIEW_AUTH_STRING` separately). The hub queries
with `GARDENER_API_AUTH_STRING` when configured; production uses the read-only API
credential. No execution depends on an open page.

The worker keeps bounded attempt observations in SQLite on heavypad and publishes
one replaceable Prefect artifact. Missing or stale observations remain visible.
Flow completion and verified VM deletion are separate outcomes.

```sh
just gardener-status                       # inspect the installed release
just gardener-image                        # build the locked runtime image
just gardener-probe --image REGISTRY/IMAGE  # create, exercise, and delete two VMs
just gardener-install COMMIT               # install a committed worker release
```

The baked image is opt-in: set `environment_mode: image`, an explicit image,
and empty `requirements` / `local_packages` in job variables after the live probe
passes. Existing deployments retain bootstrap mode. Private registries also need
provider-side pull authentication before their images can be used.

The design borrows durable execution and detachable clients from
[Albedo](https://tangled.org/okami.mom/albedo), and separation of orchestration from
runtime observation from Nebula. Prefect remains the scheduler; neither an agent
graph engine nor another dashboard database is needed. The image follows
[uv's Docker guidance](https://docs.astral.sh/uv/guides/integration/docker/): pinned
uv, a locked non-editable environment, and dependency layers before source.

## develop

```sh
uv sync                                   # workspace: flows + packages/mps
just hooks                                # install staged deployment validation
just check                                # ruff, ty, pytest, the hub's svelte-check and oxlint; what CI runs before deploying
just prefect flow-run ls                  # any prefect CLI command against the live server
just prefect deployment run 'diagnostics/diagnostics' --watch   # a run on the real worker
just push                                 # github first (installs come from there), then tangled (CI deploys)
```

## docs

| | |
|---|---|
| [operations.md](docs/operations.md) | standing up the VM and the home worker; the recipes that run the system |
| [hub.md](docs/hub.md) | the ingest → classify → transform → brief pipeline and the hub it feeds |
| [fastmcp-attention.md](docs/fastmcp-attention.md) | when fastmcp briefs fire, what "seen" means, and the staged plan for laptop triage and operator acks |
| [autofix.md](docs/autofix.md) | the gardener: failed run → pi diagnosis → pull → phi review → operator merge |
| [coding-jobs.md](docs/coding-jobs.md) | audit retained coding jobs, outputs, reviews, and telemetry coverage |
| [prefect-patterns.md](docs/prefect-patterns.md) | the mechanism behind the conventions in `AGENTS.md`, with citations |
| [agent-tooling.md](docs/agent-tooling.md) | the two MCP servers in `plugins/mps/` and what each can do |
| [prompt-caching.md](docs/prompt-caching.md) | why thousands of LLM calls cost a few dollars |
| [cost-declaration.md](docs/cost-declaration.md) | a sketch of declaring infrastructure costs on atproto |
| [audits/](docs/audits/) | point-in-time reviews of the live deployments, with a dated status of what is still open |
| [incidents/](docs/incidents/) | post-mortems |
| [archive/](docs/archive/) | shipped design notes and build logs, kept for the why; none describes the present |

[COSTS.md](COSTS.md) is the running record of what this deployment spends.

## license

[MIT](LICENSE)

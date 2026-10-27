# my-prefect-server

Personal workflows for activity briefings, agent jobs, analytics, and published
snapshots. Python flows use [Prefect](https://github.com/PrefectHQ/prefect), backed
by a self-hosted [Zig control plane](https://tangled.org/zzstoatzz.io/prefect-server).

**live:** [hub.waow.tech](https://hub.waow.tech)

## install

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), and
[just](https://just.systems/). Copy `.env.example` to `.env` and configure operator
access before using the server commands.

```sh
uv sync --locked
just hooks
```

## use

```sh
just heavypad-prefect flow-run ls
just heavypad-prefect deployment run 'diagnostics/diagnostics' --watch
```

## design

- **centralized workflows** — flow code and deployment definitions stay together;
  product repositories own the applications those flows support.
- **durable orchestration** — Prefect tracks execution; workers run jobs at home
  or in disposable cloud VMs according to their data and resource needs.
- **asset ownership** — Dagster owns dbt assets; Prefect coordinates ingestion
  and downstream work.
- **runtime credentials** — deployments name Secret blocks rather than embedding
  credentials in configuration.

## develop

```sh
just check  # deployment validation, lint, types, tests, and web checks
just push   # GitHub mirror first, then Tangled
```

Web checks require [Bun](https://bun.sh/). Read [AGENTS.md](AGENTS.md) for repository
conventions and operational constraints.

## docs

[docs/](docs/) contains operations, design notes, migration records, and audits.
Service-specific installation and recovery instructions live with [deploy/](deploy/).

## license

[MIT](LICENSE)

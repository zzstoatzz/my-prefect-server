# my-prefect-server

Personal workflows for activity briefings, agent jobs, analytics, and published
snapshots. Python flows use [Prefect](https://github.com/PrefectHQ/prefect), backed
by a self-hosted [Zig control plane](https://tangled.org/zzstoatzz.io/prefect-server).

**live:** [hub.waow.tech](https://hub.waow.tech)

## install

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), and
[just](https://just.systems/). Configure [operator access](docs/operations.md)
before using server commands.

```sh
uv sync --locked
just hooks
```

## use

```sh
just prefect flow-run ls
just prefect deployment run 'diagnostics/diagnostics' --watch
```

## develop

```sh
just check  # deployment validation, lint, types, tests, and web checks
just push   # GitHub mirror first, then Tangled
```

Web checks require [Bun](https://bun.sh/). Read [AGENTS.md](AGENTS.md) for repository
conventions and operational constraints, and [testing](docs/testing.md) for coverage
and focused test commands.

## docs

Start with [operations](docs/operations.md) for production access and deployment.
[docs/](docs/) contains design notes, migration records, and audits.
Service-specific installation and recovery instructions live with [deploy/](deploy/).

## license

[MIT](LICENSE)

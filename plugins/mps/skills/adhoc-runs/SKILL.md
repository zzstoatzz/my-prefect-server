---
name: adhoc-runs
description: Execute a flow in this repo by hand — locally for debugging, or through the API to exercise the real production worker. Use when asked to run, test, or trigger a flow outside its schedule, and to avoid corrupting production write paths while doing it.
---

# ad-hoc flow runs

Two ways to run a flow, and they test different things. Pick deliberately.

## local execution — debugging pure Python

Flow files under `flows/` are ordinary modules and most have an
`if __name__ == "__main__"` entrypoint:

```bash
uv run python flows/diagnostics.py
uv run python flows/phi_atlas.py --dry-run
```

Good for iterating on logic. Two traps:

- **Write paths.** Default local storage may be `/tmp`, not the production
  analytics dir (`/home/stoat/prefect-analytics` on heavypad). Check where a flow
  writes before running it, especially anything touching DuckDB or the spend log.
- **Secrets.** Flow code calling `Secret.load(...)` still needs
  `PREFECT_API_URL`/`PREFECT_API_AUTH_STRING` pointed at the live server —
  see [[server-access]].

Local execution does **not** exercise pull steps, job variables, or injected
secret-block values. A flow that works locally can still fail as a deployment.

## deployment execution — exercising prod

Runs on the real home-pool worker with real pull steps and job variables:

```bash
just prefect deployment run 'diagnostics/diagnostics' --watch
just prefect deployment run 'ingest/ingest' --watch
```

The form is `flow-name/deployment-name`. This is the only way to validate the
full path — and note it pulls **pushed `main`**, so an uncommitted local fix will
not be present ([[deployments]]).

`diagnostics` is the intended canary: it is retargeted to the home box
specifically to prove the worker pulls code and executes flows.

## dry-run first

Some flows take a dry-run parameter, and some default to it — `pds-records` is an
operator flow whose default deployment is dry-run list mode. For anything that
writes PDS records, deletes remote resources, or publishes to R2, run dry first
and read the output before running for real.

## before running anything with side effects

Ask what the flow actually does at the boundary. Several flows here publish
externally — PDS records, R2 objects, Cloudflare Pages deploys, Discord alerts.
Re-running them is not free and is sometimes visible to other people. Snapshot
flows upsert at `rkey=YYYY-MM-DD` and so are idempotent within a day; that is a
property of those flows, not a general guarantee.

## related

Cost flow specifics: [[infra-costs]]. Where execution happens:
[[home-pool-worker]].

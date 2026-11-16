# testing

## run the checks

```sh
just test                                 # Python suite, four workers, like CI
just test -n 0 tests/test_phi_trigger.py   # focused test without worker startup
just test --durations=20                   # find the expensive tests
just check                                # deployment contracts, lint, types, Python and hub tests
```

The test recipe installs the locked workspace's Dagster and inbox extras through
uv. Four workers bound contention between temporary servers, database processes
and subprocess tests; using every CPU previously allowed eighteen simultaneous
workers. For an already-synced environment, prefix a recipe with `UV_NO_SYNC=1`.

CI also checks the separate Cloudflare watchdog in `deploy/heavypad-watchdog`
with `bun install --frozen-lockfile`, `bun run check` and `bun test`.
The hub's checks include type/component analysis, lint and the existing Bun
tests for Access authentication, presence input, PDS resolution, Gardener data
loading and Markdown sanitization. These exercise server modules, not a browser.
See [.tangled/workflows/deploy.yml](../.tangled/workflows/deploy.yml) for the
release check sequence.

## what the Python suite tests

| Area | Behavior exercised | Representative tests |
| --- | --- | --- |
| Workflow decisions | Proposal permissions, duplicate handling, degraded outcomes, request construction and provider error classification; external APIs and model calls are generally mocked | `test_autofix.py`, `test_phi_trigger.py`, `test_costs.py` |
| Workflow integration | Our event lookup prevents duplicate autofix attempts, comment checkpoints prevent repeat delivery, and classification billing errors stop retries | `test_autofix_outcomes.py`, `test_pull_comments.py`, `test_classify_emails.py` |
| Analytics | Real DuckDB mutations/compaction, writer coordination, dbt execution, Dagster asset metadata and preserving the previous export after failure | `test_analytics_storage.py`, `test_dagster_hub.py` |
| Agent execution and source delivery | Cancellation and child-process cleanup, timeouts, immutable source selection, concurrent Git cache access and isolation from the caller's Git environment | `test_exe_runtime.py`, `test_sprites_runtime.py`, `test_deployment_steps.py`, `test_git_isolation.py` |
| Event delivery | Retry, durable checkpoints, reconnect behavior and duplicate suppression, including local HTTP/WebSocket servers | `test_watch_stream_delivery.py`, `test_pull_comments.py` |
| Deployment contracts | Manifest entrypoints, pins, delivery modes and authenticated CLI argument routing | `test_validate_deployments.py`, `test_presence_deployment.py`, `test_prefect_cli.py` |

Tests live in [tests/](../tests/). Decision and payload tests call a flow's `.fn`
when they need only its Python behavior. Tests of Prefect orchestration use
[the test harness](../tests/harness.py), which starts an isolated Python Prefect
server and database. Keep that distinction when adding coverage: starting a
server does not make a mocked request-body assertion more useful. Test decisions
and wiring owned by this repository; generic Prefect semantics belong upstream.

## limits and live probes

The local harness is not the production Zig control plane. Passing tests do not
prove that production credentials work, a release artifact is available, a
deployment is registered, or a real worker can execute it. Production verification
still needs the intended worker and an observed deployment run.

The dbt/Dagster integration tests build a small fixture project, not the full
production analytics graph. Hub tests do not execute the complete exported-DuckDB
query boundary or browser interactions. See the
[test suite audit](audits/2026-11-15-test-suite.md) for the reviewed coverage and
pruning decisions.

Standalone provider
probes such as `exe_provider_probe.py` and
`gardener_skills_probe.py` are not pytest-collected tests. They exercise live
infrastructure and are separate, explicit operations.

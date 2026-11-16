# testing

Run these from the repository root after installing the project with uv.

| Command | What it runs |
| --- | --- |
| `just test-unit` | Python unit tests and fast component checks |
| `just test-integration` | Python tests that exercise real service, subprocess, and persistence boundaries |
| `just test` | Both Python groups |
| `just check` | Deployment contracts, lint, types, both Python groups, and hub and watchdog checks |

Pass pytest arguments through any of the three test recipes. For example,
`just test-unit -n 0 tests/test_phi_trigger.py` runs a focused file without
starting parallel test workers. Use `--durations=20` to find expensive tests.

## what belongs in each group

Unit tests cover decisions, payloads, parsing, state transitions, and error
handling. Call a Prefect flow's `.fn` when checking its own decisions and
stub external calls at the boundary. Fast component checks can use small
in-process fixtures such as a temporary DuckDB database.

Mark an integration test with `@pytest.mark.integration` when it depends on
a real service or subprocess, or exercises a heavier persistence path. Use
`pytestmark = pytest.mark.integration` when the whole module fits. These tests
cover Prefect attempt deduplication and delivery checkpoints, HTTP and
WebSocket reconnects, worker process cancellation, source delivery, DuckDB
compaction, and Dagster publishing a dbt-built export. Use small fixtures and
short test-only retry or shutdown intervals.

The Prefect harness runs an isolated Python server. The Dagster smoke test uses
a small fixture project. Hub Bun tests exercise server modules and the watchdog
tests cover alert decisions. Production Zig behavior, the full analytics graph,
and browser interactions need separate verification. Provider probes such as
`exe_provider_probe.py` and `gardener_skills_probe.py` are explicit live operations.

## spindle and registration

Spindle starts two independent workflows on each push to main.
[check.yml](../.tangled/workflows/check.yml) runs the fast Python suite with four
workers, plus deployment contracts, lint, types, and the hub and watchdog checks.
[integration.yml](../.tangled/workflows/integration.yml) runs the marked Python
tests with two workers and prints the slowest cases. Separate jobs let fast
checks report while integration runs, subject to Spindle capacity.

HeavyPad requires both workflows to pass on the same main push for the exact
revision it will register. See the
[registration runbook](../deploy/heavypad-prefect/README.md#registering-main-revisions)
for the operator procedure and [deployment validation](deployments-validation.md)
for the checks that still require the real worker.

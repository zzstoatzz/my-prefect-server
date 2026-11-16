# test suite audit — 2026-11-15

The suite contained real waste, but this review does not support reducing it to
roughly 300 cases. Much of the count protects application behavior across two
custom VM workers, an inference gateway, coding automation, analytics, mail,
monitoring and home presence. Those are repository-owned implementations.

Removed 57 collected cases and 408 net test lines. The Python suite now has
862 passing cases and three credential-dependent skips across 97 modules.
The final local run took 66.79 seconds with four workers.

## scope and method

Reviewed the test-function, parameterization and assertion inventory across all
99 starting Python test modules, then inspected fixtures, implementation and
relevant history for deletion and runtime candidates. Also inspected the five
hub Bun test files, the watchdog tests and CI wiring. This was a source review
and execution audit, not mutation testing or a line-coverage measurement.

The starting point already included the preceding retired-source cleanup and
removal of one generic Prefect-state test. It contained 922 collected cases:
919 normally passing and three optional live tests. Parameterization expanded
729 test functions into those cases; “920 tests” did not mean 920 independent
functions. The resulting suite has 702 functions and 865 collected cases.

The [per-module inventory](2026-11-15-test-suite.json) records every starting
module, its category, before/after counts, removals and the cache-test rename.
Categories describe primary ownership rather than a unit/integration split.

| Area | Cases before | Cases after | What the retained tests protect |
| --- | ---: | ---: | --- |
| Agent execution and inference | 187 | 185 | VM lifecycle, cancellation, subprocess cleanup, authentication, grants and request routing |
| Coding automation | 168 | 164 | Proposal permissions, duplicate suppression, patch application, merge decisions and comment delivery |
| Analytics, memory and publication | 214 | 172 | Database writes, compaction, clustering, reconciliation, dbt/Dagster execution and publication failures |
| Costs and accounting | 61 | 59 | Provider parsing, attribution, pricing, incomplete results and spend records |
| Monitoring and recovery | 157 | 152 | Health semantics, alert episodes, backup freshness and recovery decisions |
| Mail and home presence | 70 | 69 | Classification, triage, sender parsing, presence validation and lighting decisions |
| Deployment, source delivery and credentials | 65 | 64 | Delivery modes, immutable source caching, Git isolation, secret resolution and CLI routing |
| Total | 922 | 865 | Includes three skipped live TurboPuffer cases |

## changes

| Finding | Action |
| --- | --- |
| Tests repeated decorator retry settings, a dependency constructor or `dbt --version` | Removed them. Kept HTTP failure propagation, application-specific retry classification and real dbt execution tests. |
| A snippet test sliced a string inside the test itself; another checked Python's unexpected-keyword error | Deleted both; neither exercised application behavior. |
| Diagnostic and anchor tests repeated weaker assertions against the same fixtures | Removed the weaker checks; retained exact result assertions that cover those exclusions. |
| Likes reconciliation multiplied equivalent invalid values and action casing | Reduced 44 cases to 24 while retaining both destructive actions, empty targets, bounds, mixed valid/invalid targets, strict types and non-mutation on failure. |
| Tests froze deployment names, old release pins or connector membership | Removed those snapshots. Kept delivery behavior and immutable-helper digest validation; the static deployment validator already runs separately in CI. |
| A hub test used a partial regex to compare SQL table names | Deleted it. It did not execute the web/analytics boundary and was not evidence of working queries. |
| A fly availability test did not pass observations into the code it claimed to test | Removed the misleading test; retained direct observation parsing and fleet-health tests. |
| A writer-lock wrapper test duplicated the database test | Moved its exact lease-argument assertion into the test that performs and verifies a database write. |
| Autofix branch tests started Prefect servers despite mocking external work | Called the flow body and isolated event/context access. Retained the real orchestration test that persists a failed attempt and suppresses the next duplicate. |
| Clustering invariants recomputed the same fixture | Shared the clustering result within each worker's module fixture. |
| Sixteen existing hub behavior tests were absent from CI and `just check` | Added `bun test` to both. These cover Access JWT validation, presence input, PDS resolution, Gardener loading and Markdown sanitization. |

No production implementation or deployment was changed by this test audit.
The broader working tree also contains the preceding source/documentation cleanup.

## runtime and remaining cost

The earlier 920-passing-case baseline took 105.92 seconds. Bounding local
parallelism to four workers, bypassing unnecessary Prefect servers and replacing
real backoff sleeps brought that to 74.87 seconds before this audit. Pruning and
further harness isolation produced 66.79 seconds. These are individual local
observations, not a controlled benchmark or a CI timing guarantee.

The expensive remaining cases exercise real mechanisms: dbt execution, DuckDB
compaction, HTTP delivery retries, persisted autofix outcomes and child processes
that ignore termination. Both custom workers have their own shutdown tests
because their implementations differ. Removing these to hit a count would lose
failure-mode coverage. Pure decision tests should continue to avoid the harness.

## gaps the count conceals

- The local Prefect harness runs the Python server, not the production Zig control plane.
- Dagster/dbt integration tests build a small fixture project, not the full production analytics graph.
- Hub Bun tests exercise server modules, not browser interactions or the complete exported-DuckDB query boundary. Deleting the regex check makes that existing gap explicit.
- Provider APIs are generally mocked. Passing tests do not establish live credentials, artifact availability or real worker readiness; provider probes remain separate operations.

The useful next standard is whether a case protects an owned decision or failure
mode. This audit found no evidence for deleting another roughly 550 cases simply
to reach 300. Further large reductions would need a concrete redundancy finding
or retirement of the corresponding application surface.

## validation

- Python: 862 passed, three skipped, 12 warnings; 66.79 seconds with four workers.
- Hub: 16 Bun tests passed across five files in approximately 0.35 seconds.
- Watchdog: nine Bun tests passed in approximately 0.01 seconds.
- Ruff lint/format and Python type checks passed.
- Hub type/component analysis and lint, plus watchdog type checks, passed.
- Static deployment validation reported zero errors, while explicitly leaving runtime/artifact readiness unverified.

See [testing](../testing.md) for maintained commands and boundaries. This report
and its inventory are a dated audit snapshot, not a count to enforce in CI.

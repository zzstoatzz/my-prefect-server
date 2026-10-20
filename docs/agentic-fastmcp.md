# FastMCP Agentic Workflows experiment

Cloud workspace: `prefect-technologies/marvin-bot`. Flow:
`aa69d171-a0a1-40f7-9679-4b811550ad26` (`fastmcp-issue-pr`).
The plan is versioned in `deploy/agentic-workflows/fastmcp-issue-pr.json`.
This is a manually invoked, one-issue experiment with no schedule.

The eligibility agent reads the issue, all comments/timeline, all-state PR
searches, repository instructions and relevant contracts. It excludes existing
contributions (including closed PRs), explicit contributor claims, incomplete
reports and unresolved design questions. Being the reporter alone is not a claim.
An eligible candidate reaches a separate fix agent. Deferred candidates skip it.

The fix agent must demonstrate a failing regression on unchanged main, a passing
fix, neighboring tests and all repository-required checks before publishing one
draft PR. It rechecks competing work before publication, uses a deterministic
branch, credits the reporter, and returns incomplete work instead of weakening
checks. Publication is deliberately not retried automatically. The final report
accepts failed upstream nodes so a timeout remains visible.

Agent node execution has a short runtime budget (prior runs timed out around
three minutes). Full FastMCP validation may exceed it. A blocked result is evidence
that execution needs a longer-lived coding/test environment, not permission to
publish an unverified fix. The publication path remains experimental until a real
eligible issue completes successfully.

```sh
just agentic-fastmcp validate
just agentic-fastmcp publish
just agentic-fastmcp run 5926 experiment-5926-20261020-v1
just agentic-fastmcp results RUN_ID
```

Publish only committed configuration. The helper explicitly targets Cloud and
uses the `bot` profile credential; it does not use the self-hosted server.
Reuse an idempotency key to recover an uncertain run submission. Use a new key
for an intentional repeat. Run sequentially on the same issue.

## Baseline

On October 20 UTC, the existing `fastmcp-triage` version
`06acee2c-fc08-7c39-8000-32f3ef128066` ran issue #5926 in run
`06ad6c35-8187-7563-8000-778b5d8d2ddf`. It correctly reproduced list-model
hydration failure on 4.1.1 and main `535a3e12`, and found the existing open fix
#5927. However, reproduction ran even though a corresponding PR existed. This
experiment moves that check ahead of reproduction and implementation.

Empirical gate cases: #5926 (open PR), #4866 (closed prior PRs), #4338 (missing
reproduction), and #5797 (a concrete small rollback bug; eligibility is evaluated
live rather than assumed).

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
just agentic-fastmcp verify
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

## Verified gate behavior

Version `06ad6c4f-b05d-73be-8000-54010b81c298` completed all five initial
cases with the fix node unreached and a final report present:

| Issue | Observed decision | Evidence |
| --- | --- | --- |
| #5926 | existing_pr | Open fix #5927 |
| #5797 | existing_pr | Open fix #5798 |
| #4866 | existing_pr | Closed, unmerged fixes #4872, #4874, #4876 |
| #4338 | needs_info | No runnable reproduction or pagination configuration |
| #5812 | contract_unclear | Proposed text-serialization change depends on an unmerged structured-path PR |
| #2026 | not_bug | Reporter confirmed the incoming value was already a string and reframed the request as a feature |
| #603 | eligible, then blocked | Agent reproduced a 404 on current main; reverse-proxy routing semantics remain unresolved |

Run IDs and expected eligibility decisions are in
`deploy/agentic-workflows/gate-cases.json`. `verify` re-reads those real Cloud
outputs and asserts terminal completion, the expected gate decision, the skipped
fix branch (or expected fix result) and a final report. It does not launch new
runs. These tests establish screening and investigation behavior; they do not
establish successful fix or PR publication.

For #603 the fix agent cloned main `535a3e124c3a312567168c78ed9b946e133360f6`,
completed `uv sync`, rechecked GitHub and ran `uv run python /tmp/repro603.py`.
It reported a 404 POST to the prefixed messages URL, then a 30-second shutdown
timeout. The issue's direct-to-Uvicorn reproduction bypasses the reverse proxy
that normally strips the external prefix, so the agent declined to guess at a
fix. It returned a clean worktree and no PR; the deterministic branch was also
independently confirmed absent. Full tests/static checks were not run. This run
exercised eligibility, the real reproduction stage, and reporting of blocked work.

Next experiment: establish an unambiguous regression that is still unfixed, then
measure the fix and full-validation stage. Do not enable recurring intake or
claim end-to-end PR success from these results. Gate logs also show a token cost
worth reducing: #5926 used 50,637 input and 1,982 output tokens across six model
calls; #603 eligibility used 285,381 input and 9,400 output tokens across ten
calls. These are cumulative provider token counts, not billed costs; cached-token
pricing is not accounted for. Early exit after a direct PR match and bounded
tool output are promising next changes.

The final summarizer sometimes populates `pr_url` with a pre-existing PR and calls
expected missing fix outputs incomplete. Use the eligibility and fix outputs as
the authoritative machine-readable results; a report link alone is not evidence
that this workflow created a PR.

The CLI automatically consumes stdin as a request body. The helper redirects
API subprocess stdin from `/dev/null`; without that, fetching multiple selected
outputs from a shell read-loop consumes the next row and fails JSON parsing.

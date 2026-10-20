# FastMCP Agentic Workflows experiment

Cloud workspace: `prefect-technologies/marvin-bot`. Flow:
`aa69d171-a0a1-40f7-9679-4b811550ad26` (`fastmcp-issue-pr`).
The plan is versioned in `deploy/agentic-workflows/fastmcp-issue-pr.json`.
This is a manually invoked, one-issue experiment with no schedule.

Before `run` submits anything, `scripts/load_fastmcp_skills.sh` resolves FastMCP
main to one commit and loads the full instruction files listed in
`deploy/agentic-workflows/skills.json`. Eligibility receives `triage`; preparation
receives `fix-issue`, `python-tests`, `docs`, and `review-pr`; review receives
`review-pr`, `python-tests`, and `docs`. Each bundle includes AGENTS.md, contribution
policy, and the applicable testing/versioning references. Conditional assignment,
release, and monitoring procedures are outside these nodes' scope.

The bundles are required plan inputs, wired directly to each AgentNode's
`instructions` input. Prefect includes input values in the initial model prompt;
these are complete contents, not paths the agent must fetch. Run parameters retain
the source commit, paths, SHA256 hashes, and exact text. Missing, empty, oversized,
or symlinked required files stop setup before run submission. The fix checks out
that same commit. `continue` loads review instructions from the saved patch's base,
not today's main. This loads repository skills, not the operator's local skills.
Direct API callers must supply the same required bundles; the supported launcher
constructs and hashes them deterministically. Skill delivery does not establish
that an agent follows every instruction correctly.

Skill-delivery smoke run `06ad6e86-5bc7-7131-8000-90cc1d4d9425` used main-plan
version `06ad6e85-8fa7-791d-8000-73c50df36422` and FastMCP commit
`535a3e124c3a312567168c78ed9b946e133360f6`. API readback confirmed all three
stored bundles exactly matched the fetched contents and hashes (5 eligibility,
9 preparation, and 8 review files). Eligibility excluded #5926 because of #5927;
all downstream nodes were unreached. Its first model call received 10,976 input
tokens, and the invocation completed in 18.68 seconds without retries. This tests
delivery and screening, not another full issue-to-PR run. Continuation version
`06ad6e85-c93b-7856-8000-21db74afc054` also requires the review bundle.

Nine loader/wiring tests use real temporary Git repositories: exact content/hash
preservation across a moving branch, per-role file sets, invalid revisions,
missing skills/references, empty/oversized files, and symlinks. The existing eight
publication tests also pass.

First published result: [draft PR #5928](https://github.com/PrefectHQ/fastmcp/pull/5928),
clarifying SSE proxy configuration for issue #603. This experiment required
infrastructure repairs and explicit resumptions; it does not establish reliable
unattended backlog processing.

The eligibility agent reads the issue, all comments/timeline, all-state PR
searches, repository instructions and relevant contracts. It excludes existing
contributions (including closed PRs), explicit contributor claims, incomplete
reports and unresolved design questions. Being the reporter alone is not a claim.
An eligible candidate reaches a separate fix agent. Deferred candidates skip it.

The preparation agent demonstrates a failing regression and passing fix, or
executes a supported configuration before correcting its documentation. It
returns an uncommitted patch pinned to the exact base SHA. A DeploymentNode runs
`fastmcp-validate-patch/cloud-validation` in the ready `managed` pool. That flow
applies the exact patch, enforces its scope, runs the full suite and static checks
(plus docs checks when applicable), and rejects failures or formatter changes.
Only its `completed` output reaches the review agent, which reviews the unchanged
diff and returns a decision and PR wording. A separate deterministic deployment,
`fastmcp-publish-validated-patch/cloud-publication`, verifies the completed
validation run and exact patch hash, rejects stale reviews or changed/assigned
issues and corresponding PRs, runs commit hooks, and opens one credited draft PR
on `agentic/issue-N`. Network retries recover the deterministic branch/PR and
verify its tree/head before proceeding. Agents do not run the full suite or
perform publication. There is no summarizer agent; selected node outputs and the
publication child's PR artifact are the evidence.

Agent node execution is limited to about three minutes. The managed deployment
has a 40-minute flow limit, a 45-minute container limit, four CPUs and one
concurrent run. It has no schedule or GitHub write credential. Its source is
commit-pinned in the separate `deploy/agentic-workflows/prefect.yaml`; it does not
use the old self-hosted triage worker. Publication has a separate 20-minute flow
limit and loads its GitHub credential from the existing Cloud Secret block.
Publication has been exercised on that one documentation issue and remains experimental.

```sh
just agentic-fastmcp validate
just agentic-fastmcp deploy-validation
just agentic-fastmcp publish
just agentic-fastmcp publish-continuation
just agentic-fastmcp run 5926 experiment-5926-20261020-v1
just agentic-fastmcp results RUN_ID
just agentic-fastmcp verify
```

Publish only committed configuration. The helper explicitly targets Cloud and
uses the `bot` profile credential; it does not use the self-hosted server.
Reuse an idempotency key to recover an uncertain run submission. Use a new key
for an intentional repeat. Run sequentially on the same issue.
`run ISSUE KEY CONTEXT_FILE` supplies optional operator context for a follow-up
experiment. It does not bypass eligibility or required validation.

`continue SOURCE_RUN VALIDATION_RUN KEY` resumes an eligible saved patch through
the separate `fastmcp-publish-patch` plan after a terminal preparation run whose
publication node was never reached. It verifies that the designated validation
deployment completed on exactly those saved parameters, then reviews and
publishes that immutable patch. It avoids repeating triage, reproduction, and
successful deterministic validation after a downstream infrastructure failure.
`retry-publication REVIEW_RUN KEY` retries only a failed deterministic publication.
It reads the stored approval and the review's actual incoming edges, verifies that
those immutable inputs match the completed validation, and rechecks GitHub before
writing. No agent copies or hashes patch bytes during this recovery.

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

These baseline cases do not establish successful publication. Do not enable
recurring intake based on screening results. Gate logs also show a token cost
worth reducing: #5926 used 50,637 input and 1,982 output tokens across six model
calls; #603 eligibility used 285,381 input and 9,400 output tokens across ten
calls. These are cumulative provider token counts, not billed costs; cached-token
pricing is not accounted for. Early exit after a direct PR match and bounded
tool output are promising next changes.

The initial summarizer sometimes populated `pr_url` with a pre-existing PR and called
expected missing fix outputs incomplete. Use the eligibility and fix outputs as
the authoritative results for those historical runs. The current plan removes
that summarizer; its PR artifact is created by the deterministic publisher.

The CLI automatically consumes stdin as a request body. The helper redirects
API subprocess stdin from `/dev/null`; without that, fetching multiple selected
outputs from a shell read-loop consumes the next row and fails JSON parsing.

## Managed validation experiment

Follow-up #603 run `06ad6c80-2622-763d-8000-8f78ab5e8086` tested the documented
proxy configuration question but its combined fix/check node timed out after
179.96 seconds, returning no patch. The report still completed and exposed the
failure. Main's successful test workflow for `535a3e12` took about five minutes
([run 39323885441](https://github.com/PrefectHQ/fastmcp/actions/runs/39323885441)),
so full validation was moved into a normal managed deployment rather than
weakening the checks to fit an AgentNode.

Deployment `5f1e86fb-d975-47e5-a85d-a45b3dd04865` was initially pinned to source commit
`c20858de7d565bec3a772f93943934304c586a21`. Controlled run
`06ad6ca1-79f8-7b74-8000-f99041d5e76e` successfully pulled that source, installed
the required tools, fetched the FastMCP base, applied a valid patch adding an
out-of-scope root file, and failed with the expected scope error before tests.
No GitHub changes were made.

Plan version `06ad6ca2-26b6-779e-8000-72c265f689ef` separates preparation,
validation and publication. The #603 follow-up is run
`06ad6ca2-515d-72fe-8000-21835240a111`. Preparation returned a documentation
patch, but child validation `dd00977f-2b61-52e4-a0db-a44094032e44` rejected a
serialized hunk with an incorrect line count. Publication was never reached.
Both validator and publisher now use `git apply --recount` to infer hunk counts
from the supplied lines without changing the patch's content. The same saved
patch passes Git's applicability check with this option.

An independent local two-server reproduction also confirmed the proposed
clarification: direct access advertises a prefixed SSE messages URL that returns
404, while a prefix-stripping proxy successfully lists and calls the add tool,
returning 3. This establishes the configuration behavior, not publication success.

Continuation `06ad6cd2-111e-7869-8000-b0f91292efed` validated that same patch in
child `4a3b293e-f8a0-52c4-95aa-263a7104dc76`: 9,620 passed, 10 skipped, 16 xfailed;
all static hooks passed; 67 documentation tests passed; Mintlify found no broken
links. Base `535a3e124c3a312567168c78ed9b946e133360f6`, patch SHA256
`50a65c4956ea4d3c8adebd312ad6b7c292664cd8e102ad2f36eed1b186cf0ae2`.
Its publication agent then timed out before creating a branch or PR. This exposed
another misplaced responsibility: deterministic Git/hooks/API work now runs in
deployment `b74cd9ba-4596-41e8-84fc-6f0620bd8d4b`, with a separate review AgentNode.
Live read-only checks verified the stored validation/digest and rejection of a
wrong digest, stale review, assigned issue, and post-review timeline change.

Review `06ad6d10-dedd-7ce8-8000-9c4fe8d4af9f` approved the documentation change,
but the original publication contract asked the model to copy and hash the patch;
that copy changed the bytes and the deterministic check correctly rejected it.
Publication now resolves the review's actual workflow inputs and hashes them
directly. Live checks passed for that binding and rejected altered approval text.
The first recovery also exposed a repository-reference bug: a Dependabot release
note linked `astral-sh/setup-uv#603`. The publisher now distinguishes foreign links
from FastMCP references; eight regression tests and the live GitHub preflight pass.

## First published result

Deterministic publication run `06ad6d36-96a7-7934-8000-647c4fcf023c` completed
and opened draft #5928 at head `7ab867013987b90e150b3601e7e8d7bf0ac913f3`.
Its source deployment is pinned to `b433570eed5c9ea99e65868f57bc822ddda67338`.
Independent GitHub inspection confirmed the same base, the single intended docs
change, draft status, and reporter co-authorship in both commit and PR body.
Tree `628ca3cd6f01ece77331454e404f4705f9aa481d` exactly matches the independently
applied validated patch. The publication node ran commit hooks successfully.

The change is wanted as a configuration clarification: the direct SSE failure and
working prefix-stripping proxy were independently executed. It changes no runtime
behavior or compatibility promise. GitHub CI/review status must be checked at the
current head; a successful Cloud validation is separate evidence.

At that head, all running GitHub checks completed successfully (editorial-only
matrix entries preserve their required check names while skipping expensive test
steps); no substantive bot or human review had arrived. The other open change
to this page, #5653, renames the hosting product in a different section and does
not overlap this clarification.

Replay `06ad6d3f-13cd-70b3-8000-a421df26c396` completed using the same validated
tree and existing draft. Its task records contain no push or PR-creation task;
GitHub still reports exactly one branch PR, #5928, at the same head. Recurring
intake remains off. The next experiment should be another manually selected
issue, measuring outcomes and agent runtime before adding a schedule.

## Bounded intake experiment

The bounded plan adds deterministic GitHub collection before submission, a narrow
PR-matching AgentNode, and a separate reproduction AgentNode. The final review is
an independent adversarial pass over the immutable validated patch. Existing PRs,
including gate-closed contributions, produce `existing_work` with a
`review_existing_contribution` disposition in the batch ledger. Their full review
is a subsequent work item; the overnight batch prioritizes missing fixes and does
not claim to have reviewed those PRs.

The collector retains complete issue comments/timeline, all-state reference search,
focused title searches, candidate PR bodies and revisions. Explicit fixing links
permit an early match attempt. The matching agent may perform two additional
symbol/error searches for unlinked work. Retrieval failures, truncated search,
over 30 candidates or 250 KB of evidence stop submission for inspection. The
inventory used to choose a queue is a bounded selection aid, not proof of no PR.

```sh
just agentic-batch init /absolute/state/night-YYYYMMDD ISSUE1 ISSUE2
just agentic-batch run /absolute/state/night-YYYYMMDD
```

Each batch has at most 12 named issues, one active issue, a target/limit of three
new drafts, and a two-hour publication window. The controller stops after three
consecutive execution failures, queue exhaustion, or observed usage reaching
8 million input / 160,000 output tokens. Token limits are checked between issues;
one issue's bounded agent calls can cross that threshold. Time and publication
limits are independently checked inside the publisher immediately before pushes
and PR creation. The publisher uses a batch-specific branch and counts all PR
states toward the cap. Its deployment concurrency is one. Publication always
creates a draft. Merges, releases, assignments, comments and marking ready are
out of scope.

The policy and ledger live outside the checkout. A filesystem lock excludes a
second controller. A reserved issue/idempotency key is persisted before submission;
uncertain submission stops the batch and resumes with that same key. The controller
records node outputs, logs, token counts and actual GitHub PRs. At the deadline it
requests cancellation of its active workflow and direct deployment children. Worker
and AgentNode timeouts remain additional bounds. There is no recurring schedule.
Do not reinitialize a batch to bypass its limits. A later batch requires fresh
operator authorization.

The publisher now requires the batch policy stored on the reviewed workflow run;
legacy or unbounded publication requests fail. A continuation inherits the source
run's original policy and expiration. The experiment deploys an immutable review-
branch commit while its implementation PR awaits the maintainer; it does not land
changes on main.

The publisher verifies the original run's stored `match.clear` output and rechecks
the collected PR revisions, bodies, states, and update timestamps. Unchanged prior
art keeps the matcher's semantic decision. New issue-linked contributions or changed
prior art require another review. This avoids rejecting an approved follow-up merely
because the issue references an earlier partial migration. An entirely new, unlinked
PR can still race these checks; intake matching and publication checks reduce that
window but cannot provide an atomic GitHub-wide exclusion.

### 20 October 2026 batch result

`night-20261020` finished at 07:58:46 UTC after 12 distinct issues and one search
recovery (13 runs). It produced **zero new FastMCP PRs**. Twelve runs completed;
#847's reproduction timed out after 180 seconds. No run remains active and no
recurring intake was added. The PR-output goal was not achieved.

Recorded `gpt-6-luna` usage was 3,586,000 input and 59,376 output tokens. The last
model log in the timeout has no usage counters, so this is observed usage rather
than a bill or exact total. No candidate reached deterministic full validation or
publication in this batch.

The useful case was #2132, run `06ad71be-a965-7ac0-8000-23a9c1cf10ae`: the
reproduction and fix agents both observed `asyncio.create_task` fail under Trio.
The fix agent stopped because the client also relies on asyncio-specific session,
monitoring, and cancellation operations; a local one-line substitution would be
incomplete. The regression diff is retained in the ledger.

#1815 and #2837 had existing contributions (#1834 and #2898). The documented
replacement for #1822 worked through Uvicorn. #4358 and #2521 had contributor
claims; #2821 lacked reproduction information. #3164, #1653, #2293, and #2879
needed contract or supported-version decisions. #1653's first search was too broad;
its single recovery used the corrected matcher within the original policy window.

51 focused tests, Ruff, deployment contracts, and Cloud plan validation passed.
Live main plan: `06ad71ab-4da4-71d5-8000-f2ea659610df`; continuation:
`06ad7199-fce2-7b98-8000-f91765205e00`. Managed deployment source:
`59cbb52fbff469b8b2af9229e162cbc3150ed5f0`, an immutable review-branch commit.
The changed publisher has focused tests but still needs a qualifying live patch
for end-to-end verification. The next bounded experiment needs a confirmed current,
tractable issue and tighter deterministic preparation/probe execution.

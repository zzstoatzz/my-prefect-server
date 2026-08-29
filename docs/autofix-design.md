# autofix: failed flow run → pi → PR (design spike, 2026-08-29)

status: **spike only.** nothing here is built or wired. no automation was
changed and pi has not touched anything.

## the loop we want

```
prefect.flow-run.Failed ──► automation ──► run-deployment: autofix
                                                  │
                             ┌────────────────────┤
                             ▼                    ▼
                    gather context         screen (policy judge)
                    (state msg, logs,             │
                     deployment, code)            ▼
                                            pi, sandboxed, in a scratch clone
                                                  │
                             ┌── needs a human ───┤─── has a fix ──┐
                             ▼                    ▼               ▼
                      DM nate (discord)    give up: Degraded    flow builds patch,
                      wait for reply       + report event       opens tangled PR
                                                                 (open-pr skill prose)
                                                  │
                                                  ▼
                                   hub.autofix.* events → hub shows it,
                                   discord gets one message with links
```

what today's manual loop is: discord alert → copy/paste into a claude/pi
session → fix → PR. the design replaces the copy/paste with an automation and
keeps the review.

## what already exists (most of the hard part)

| piece | where | state |
|---|---|---|
| failure automation | `deploy/automations.yaml:26` `flow-run failure -> discord` | fires on Failed/Crashed/TimedOut, action is send-notification only |
| event → run-deployment precedent | `deploy/automations.yaml:72` `fastmcp direct ask -> brief` | exactly the shape autofix needs; `apply_automations.py` resolves `deployment: <name>` |
| sandboxed pi runner | `packages/mps/src/mps/pi.py` | `minimal_env()` (no worker env), tool allowlists, `screen_prompt()` haiku policy judge that fails closed |
| pi → tangled PR without a write credential | `flows/pi_pr.py` | the *flow* builds the patch and publishes `sh.tangled.repo.pull`; pi never pushes |
| human-in-the-loop | `flows/pi_agent.py:88` | `pause_flow_run` before `tool_mode=full` |
| secrets | `mps.secrets_plugin`, `prefect-block://` sentinels in `prefect.yaml` | resolved at flow-run start, per deployment |
| pi on the worker | heavypad `~/.local/bin/pi` 0.74.2 | auth: `openai-codex` only. no anthropic login; `anthropic-api-key` block is injected as env for the `pi-agent` deployment |
| hub bus | `hub.brief.ready`, `github.*` events | hub already consumes custom events |

so the new work is: (1) a context-gathering step, (2) the "ask nate" channel,
(3) the PR-authoring prose, (4) hub rendering, (5) the automation line.

## open design questions, with a recommendation each

### 1. what pi receives

the discord body today is `{{ deployment.name }} run {{ flow_run.name }}
reached {{ state.name }} - {{ state.message }}`. that is what you copy/paste,
and it is not enough for an agent. the `run-deployment` action templates the
triggering run's `{{ flow_run.id }}` into an `autofix` parameter — the same
template mechanism the discord action uses for `{{ state.message }}`, so
nothing new is invented. the point is to pass the *id*, not the rendered
alert text: the flow then fetches everything else over the API with the
orchestrator credential *before* pi starts, and pi gets a rendered brief:

- deployment name, entrypoint, parameters, work pool
- state message + the last N log lines (`/api/logs/filter`)
- the failing task run(s) and their tracebacks
- the git sha the run pulled (`pull` step in `prefect.yaml`)
- prior autofix attempts on the same deployment (from events, see §5)

pi does not get the API credential. it reads the brief and the clone. this
keeps `mps.pi`'s two invariants intact.

### 2. how pi asks you questions — the channel

options considered:

| option | receive replies? | infra | verdict |
|---|---|---|---|
| discord webhook (today) | no | none | send-only, fine for *reports* |
| discord bot (gateway or interactions endpoint) | yes | a bot token + a tiny listener | **recommended for asks** |
| bluesky chat DM (`chat.bsky`) | yes | phi's creds exist | mixes phi's identity with ops; `pi_pr.py` explicitly refuses this |
| prefect `pause_flow_run` + UI resume with input | yes | none | works, but it's "go to the UI", not "the agent comes to me" |
| the hub as a chat surface | yes | build it | future, per your note; not first |

recommendation: **discord bot with an interactions endpoint** (HTTP, no
gateway process to babysit). the autofix flow posts a DM as the bot with the
question and a message-component (buttons for yes/no/skip, a modal for free
text). the reply lands on an HTTP endpoint we already own (the zig prefect
server, or a cloudflare worker) which **emits a prefect event**
`autofix.answer` carrying `{flow_run_id, answer}`. the flow, meanwhile, is
*paused* on `pause_flow_run(wait_for_input=...)` or polling events — see §3.

why this over a gateway bot: no long-lived socket on heavypad, the reply is
a normal event on the bus, and the hub sees the question and the answer
without extra plumbing.

what the ask looks like (natural language, cited, as you asked):

> **strata-hourly-d33345ba failed** (ingest-segment, 27m ago)
> the header checksum for segment 0412 disagrees with the archive's listing
> (log lines 88-94). two ways to go:
> 1. treat this as compaction in progress and back off (like commit 99a66b7 did for the listing case)
> 2. re-list and re-ingest the segment
> which do you want? i'll open a PR either way; option 1 is smaller.

### 3. the wait

pi is a subprocess; it cannot suspend itself mid-thought across a
`pause_flow_run`. two workable shapes:

- **(a) pi runs in rounds.** round 1: `tool_mode=read-only`, pi must end
  with either `PLAN:` or `ASK:`. if `ASK:`, the flow DMs, pauses, resumes
  with the answer, and runs round 2 with `--session` continuation (pi
  supports `--session <id>` and `--session-dir`). rounds are bounded (say 3).
- **(b) an `ask_operator` custom tool** loaded via `pi -e ask.js` that
  blocks on the reply. simpler for pi, but the flow can't pause (the
  subprocess is alive), the worker slot is held for hours, and a hung reply
  kills the run at `timeout_seconds`.

recommendation: **(a)**. it composes with `pause_flow_run` (which the guard
already respects — paused runs survive worker restarts), and every round is
a discrete, logged, screenable prompt.

### 4. what pi is allowed to do

- rounds before approval: `read-only`. it can clone, read, grep, run nothing.
- the fix round: `full` inside the scratch clone only, no credentials, same
  as `pi_pr.py`. tests run there.
- **never**: kubectl, ssh, `just deploy`, the prefect API, secrets. this is
  enforced by `minimal_env()` + the judge + the fact that the flow — not pi —
  does the publishing. keep it that way; do not hand pi a "fix infra" tool.
- infra fixes (restart a worker, bump a resource) become **PRs against the
  repo that declares that infra**, never live actions.

### 5. output: a PR plus events

- PR via `mps.tangled.create_pull`, as `pi_pr.py` does. title/body composed by
  pi under the `open-pr` skill (`--skill <path>`; vendor the skill file into
  the repo so the worker has it — it lives only in `~/.claude/plugins` today).
  the skill's `gh` mechanics don't apply (primary remote is tangled); only
  its prose rules are used.
- events, one per lifecycle step, resource `autofix.<flow_run_id>`:
  `autofix.started`, `autofix.asked`, `autofix.answered`,
  `autofix.proposed` (with PR uri), `autofix.gave-up` (with reason).
  the flow itself ends `Completed(name="Proposed")` / `Completed(name="GaveUp")`
  — never Failed, or it would trigger itself.
- discord gets **one** message per attempt at the end, linking the PR and
  the run. the noisy per-failure alert can then be scoped down to
  "autofix gave up" rather than every failure.

### 6. automated review

the PR needs a review before you look. two cheap layers:

1. CI on the tangled pipeline already runs tests on push; PRs from patches
   get the same.
2. a second `pi` (or `claude -p`) pass with the `code-review` skill, read-only,
   posting its verdict as a PR comment via the flow. same sandbox rules.

do not auto-merge. ever. the design's safety story is "worst case is a bad
diff in a PR you review".

### 7. loop guards

- the automation must exclude the `autofix` deployment itself
  (`match_related` on deployment name, or a `flow_run.tags` filter).
- one attempt per (deployment, sha): record attempts as events and read them
  in the context step; second failure on the same sha → gave-up with a
  pointer to the first PR.
- `threshold`/`within` on the trigger to absorb bursts (four strata failures
  in two hours today should be *one* autofix run, not four).
- `screen_prompt` on the rendered brief — it's built from logs, which are an
  injection surface (a flow that logs untrusted input could steer pi).

### 8. model/provider

heavypad's pi has only the `openai-codex` login. `pi-agent` injects
`ANTHROPIC_API_KEY` from the block, which pi reads from env. either works;
pick per deployment like `pi_pr.py` does. the judge stays haiku.

## the hub

your note: the hub should render *the work that is supposed to be
happening, and whether it is*, and eventually be a place to talk to the
agent. concretely for this design:

- **schedule vs reality**: the hub already knows the deployment table
  (`docs/hub.md`). add a "runs" panel: per deployment, last expected run,
  last actual, state. this is a read of `/api/flow_runs/filter` + the
  deployment schedule, no new data source.
- **autofix timeline**: the `autofix.*` events above render as a thread per
  incident: failure → what pi asked → what you answered → PR → review → merged.
  this is the same bus the brief already reads, so no new pipeline.
- **conversation**: the DM channel is discord *first* (agent comes to you);
  the hub renders the same exchange from events, so a hub chat surface later
  is a second *emitter* of `autofix.answer`, not a redesign.

so: every step of autofix emits an event, and the hub grows a runs panel and
an incident thread. that is what keeps it from being left behind.

## proposed order of work (each step reviewable, none done yet)

1. `autofix` flow, **dry-run only**: gather context, run pi read-only,
   print the brief + pi's diagnosis, emit events. no DM, no PR. run it by
   hand against a real failed run id (strata `d33345ba…` is a good first).
2. the discord ask channel (bot + interactions endpoint → event) — test it
   with a hand-triggered run before wiring it to failures.
3. PR creation (reuse `pi_pr.py`), review pass, vendored `open-pr` skill.
4. hub: runs panel + incident thread from events.
5. **last**: the automation in `deploy/automations.yaml`, scoped to a small
   allowlist of deployments (strata, mcp-atlas — the two recurring
   offenders in the last week), with threshold/within and the self-exclusion.
6. grafana: alertmanager is disabled (`deploy/monitoring-values.yaml:2`);
   when it's on, a contact point that POSTs to `/api/events` makes grafana
   alerts ordinary bus events the same automation shape can consume.

## things i verified and things i did not

verified: automation shapes on the live server, pi version/auth/flags on
heavypad, the existing `pi_*` flows and `mps.pi` invariants, the failure list
for the last two weeks, that no discord *receiving* path exists anywhere.

not verified: that pi 0.74.2 honors `--session` continuation in `-p` mode
the way the docs of 0.84 describe (check before committing to §3a); discord
interactions-endpoint DM behaviour for bots (DMs to a user require the user
to share a server with the bot or have DMed it first — confirm).

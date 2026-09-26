# fastmcp attention

how fastmcp activity reaches the operator today, what "already seen" means at
each step, and the staged plan for a laptop triage agent and an operator ack
ledger that phi can read.

## the path today

```
github notifications ──► watch-fastmcp (*/5) ──► github.<reason> events
                                                        │
          ┌─────────────────────────────────────────────┤
          ▼                          ▼                  ▼
  activity -> brief          direct ask -> brief   cron 0 */4 * * * (UTC)
  8 events / 3600s, 6h       1 event, 2h window     6h window
          └──────────────┬───────────┴──────────────────┘
                         ▼
                  fastmcp-brief ──► hub.brief.ready ──► brief ready -> discord
```

- `watch-fastmcp` polls `/repos/PrefectHQ/fastmcp/notifications` with
  `If-Modified-Since`. A 304 emits nothing. Any other response emits one event
  per **unread** thread in the list, whether or not that thread moved.
- `fastmcp-brief` reads the window back off the bus, drops thread versions it
  has already looked at (`unbriefed`), drops closed and merged threads
  (`enrich`), asks Haiku to rank what is left, and emits `hub.brief.ready`
  only when something survives.
- `brief ready -> discord` is the only automation that sends fastmcp activity
  to a person.

## what "seen" means

`fastmcp_briefed_threads` (a Prefect Variable) maps thread_id → updated_at for
every thread the brief looked at, **including the ones the model discarded**.
It records what the pipeline has looked at. Nothing records what the operator
has seen.

The Variable is trimmed by `fit_briefed` to stay under Prefect's 5000-character
limit, oldest versions first. A trimmed thread that is still unread is news
again on the next run (`TestUnbriefed` pins both behaviors).

## measured, 2026-09-25

From the live event log:

- The two briefs of the evening of 2026-09-25 (19:00 and 23:00 CDT) were both
  the 4-hour floor (`hub.brief.ready` at 00:00 and 04:00 UTC). Off-hour briefs
  in the same week (17:20, 06:50 UTC) came from the volume automation.
- The watcher re-emits unchanged threads. #5280 was emitted at 20:45 and again
  at 03:35 UTC with the same `updated_at`. In one 50-event page, 12 of 38
  thread versions appeared twice. The volume automation counts events, so
  repeats count toward its threshold of 8.
- Latency is bounded by the floor. #5280 moved at 04:06 UTC, six minutes after
  the 04:00 brief. Unless the volume automation trips, the next look is 08:00.
- `recent_github_events` reads one page of 200 events without paging. The week
  of 2026-09-24 held 75 `github.*` events in total, well inside that bound.
- Discord showed an "Issues · PrefectHQ/fastmcp" preview card under the
  single-item brief at 00:00 UTC even though the link was masked. Observed
  once; `render()` assumes masked links never unfurl.

## where this is going

Each stage is additive and ships (commit, push, deploy, verify) before the next
starts. The Discord brief is unchanged until the last stage.

1. **structured brief.** `hub.brief.ready` carries `surfaced` (number, url,
   thread_id, updated_at, severity) next to the rendered `brief`.
2. **laptop triage** (live since 2026-09-26, drafts only). `fastmcp-triage` runs on `laptop-pool`
   ([deploy/laptop-worker](../deploy/laptop-worker/README.md)), triggered by
   `hub.brief.ready`, with `limit: 1` and `collision_strategy: CANCEL_NEW`.
   The event is a wake-up only, as Prefect's debouncing guide
   (`v3/advanced/debouncing-events.mdx`) prescribes: each run re-reads the last
   day of `surfaced` items and each thread's current state, skips what is
   closed or already triaged at its current version (receipt: artifact
   `fastmcp-triage-<number>`), so a run that queued while the laptop slept does
   current work and a backlog collapses to one run. The zig server does not
   implement `schedule_after`; nothing here relies on it.

   Each thread gets headless Claude Code in a fresh local clone of fastmcp,
   working through the repository's own skills (fix-issue for bugs,
   review-issue and code-review for other people's pull requests). The agent
   chooses `none`, `draft`, or `ready`, and an urgency, and says why; that
   judgment is deliberately not hard-coded, so it can widen as the agent earns
   it. The boundary is mechanical instead:

   - Claude Code's sandbox limits its network to PyPI and denies reads of the
     home directory, which also hides the keychain (verified 2026-09-26:
     `gh`, `git ls-remote`, and `curl api.github.com` fail inside; the gh
     keychain item reads as not found). Its Read/Edit tools are limited to the
     clone, and `.git` and `.claude` are unwritable, so nothing it leaves
     executes when the flow runs git afterwards.
   - The flow publishes: it commits with hooks disabled, refuses anything
     containing the operator's GitHub token or a token-shaped string, pushes,
     and opens the pull request. It never runs code the agent wrote; the pull
     request's CI does. It never merges, comments, assigns, or edits an
     existing pull request, and never opens one for someone else's.
   - The run uses `--setting-sources project` and `--strict-mcp-config`, so
     the operator's own hooks and claude.ai connectors are absent.

   - Someone else's claim skips a thread before any agent runs: an assignee,
     an open PR by anyone, or a contributor PR the issue-link gate closed
     (`missing-issue-link`). The reporter's first claim is in the prompt.
   - Checks: the agent runs prek in the sandbox until a run changes nothing
     and reports it (`checks_clean`). The flow re-checks the staged change
     with ruff and codespell at main's pinned versions via isolated `uvx`;
     it never runs prek itself, whose system hooks (ty, loq) would execute
     the agent-built venv. Any failure opens a draft with a warning on top.
   - `allow_ready` (default off) opens every PR as a draft, keeping the
     agent's ready/ship-now advice in the body. Agreed with the fastmcp
     session on 2026-09-26 while the operator is away for two weeks.

   Results land in Discord through `triage ready -> discord`, each with the
   session to `claude --resume`.
3. **ack ledger.** The operator's acks live in a private space on
   pds.zat.dev, owned by a dedicated operator account (the main account's PDS
   does not serve spaces, and public records would reveal activity on private
   subjects). One record per subject, keyed deterministically, with the acked
   version (`updated_at`) and time. A subject is news when it has never been
   acked or has moved since the acked version.
4. **phi reads the ledger.** Read-only membership in the space. Whether phi
   migrates to pds.zat.dev for this is decided at this stage on its own merits.
5. **retire the duplicates.** The brief skips acked subjects and the Variable
   is replaced by the ledger's two facts: looked at, and acked.

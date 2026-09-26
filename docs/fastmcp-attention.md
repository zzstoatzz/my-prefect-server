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
2. **laptop triage.** A `laptop-pool` process worker on the operator's Mac,
   kept alive by launchd. An automation on `hub.brief.ready` runs a
   `fastmcp-triage` deployment there with `concurrency_limit: 1` and
   `collision_strategy: CANCEL_NEW`. The event is a wake-up only: each run
   reads current thread state and the ledger, so a run that queued while the
   laptop slept does current work and a backlog collapses to one run. Claude
   runs headless with read-only tools; the session id and result land as a
   Prefect artifact and a `hub.triage.ready` event.
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

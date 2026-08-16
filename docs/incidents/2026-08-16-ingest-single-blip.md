# One dead upstream discarded the whole ingest run, August 2026

Status: fixed and production-verified on 2026-08-16 (`0519b82`).

## Impact

`ingest` failed 7 times in 337 runs over the preceding month. Each failure
discarded the entire run — including the sources that had already been fetched
successfully — and paged Discord via the `flow-run failure -> discord`
automation.

The trigger that prompted the investigation:

```
ingest run ingest-81cdf915 reached Failed - Flow run encountered an exception:
ReadTimeout: The read operation timed out
```

## Root cause

One page of the PDS likes pagination hit a read timeout:
`flows/ingest.py` → `fetch_nate_likes` → `mps/likes.py` → `client.get(XRPC, ...)`.

That alone should not have been fatal. Two properties made it so:

1. **No retries anywhere.** `ingest` defined 16 tasks; 15 were a bare `@task`.
   The one decorated task (`fetch_issue_or_pr`) configured caching and result
   persistence but also no retries. Every external fetch was single-attempt.
2. **Every fetch was joined unconditionally.** The flow did
   `likes = likes_future.result()`, so a raised exception propagated and
   aborted the run — discarding the github, tangled and phi rows that the same
   run had already fetched.

The history shows this was not specific to the likes call:

| when | failure | where |
| --- | --- | --- |
| Aug 16 | `ReadTimeout` | `fetch_nate_likes` → PDS `listRecords` |
| Aug 8 (×5) | `301 Moved Permanently` | `fetch_issue_or_pr` → GitHub |
| Aug 5 | `ReadTimeout` | `fetch_issue_or_pr` → GitHub |

The GitHub 301 cluster was already fixed separately — `gh_client` sets
`follow_redirects=True`, because renamed repos 301 to a numeric
`/repositories/<id>/` URL. So the live failure mode was purely "transient
network error, no retry", and it had already bitten two different endpoints.

## Fix

Three changes, in `0519b82`:

- `NETWORK_RETRIES` (3 attempts, jittered `[2, 5, 10]`) on all 8
  network-touching tasks. This alone absorbs both historical ReadTimeouts.
- A source still down after its retries now **degrades instead of failing**.
  `_tolerate()` substitutes an empty default — every persist was already
  guarded on an empty source — and the flow returns
  `Completed(name="Degraded")`. See `docs/prefect-patterns.md`.
- The hourly likes walk is bounded to the 3 newest pages. `listRecords`
  returns newest-first (verified against the PDS) and likes upsert by
  `at_uri`, so re-walking history back to 2024 every hour only added chances to
  hit the timeout.

GitHub deliberately still fails the run: it is the primary source here, and
quietly degrading it would mask a sustained outage or a bad token.

## Notes for next time

- A task that catches its own transient errors never retries. The `try/except`
  has to live at the join point, not inside the task.
- `estimated_start_time_delta` (added to prefect-server the same day) is now
  the cheap way to find genuinely stuck scheduled work. Do **not** infer
  "stuck" from a run's `created` timestamp — the scheduler emits runs for
  infrequent schedules months ahead, so an old `created` with a future
  `expected_start_time` is entirely normal. That misreading nearly led to
  deleting a set of healthy monthly runs.

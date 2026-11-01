# transform stalled under the Dagster unit's MemoryHigh

2026-11-01. The 16:01 and 17:01 UTC `transform` runs failed at Dagster's
1500-second limit; hourly builds had taken 40–80 seconds.

## what happened

- At 16:04 UTC systemd-oomd killed `dagster-hub-daemon.service` (152
  processes) mid-export, leaving `hub.duckdb.new` behind.
- Later runs reached dbt and stalled in `int_github_issues_scored`: 1007 s for
  830 rows whose query takes 0.4 s. The step's processes sat in
  `mem_cgroup_handle_over_high`; the unit's `memory.events` counted 207,208
  `high` events.
- The unit sets `MemoryHigh=4G`, `MemoryMax=8G`. dbt-duckdb set no
  `memory_limit`, so DuckDB sized its buffer pool from the cgroup (about 80% of
  8G) and dbt grew past 4 GB against a 5.1 GiB, mostly dead `analytics.duckdb`.
  The kernel throttled rather than failing it.
- The model's time had crept up for a day before it broke (24 s, 47 s, 135 s,
  then 1007 s) against a usual 8 s.

## evidence

On a copy of the database inside a scope with the same limits, the build took
1195 s as deployed and 5–7 s with `memory_limit: 2GB`, including a run on a
fresh copy with nothing cached. The first production run after the fix
(19:01 UTC) took 30.7 s with zero new `high` events.

## changes

- `3ef44ac`: dbt profile, spend import and hub export cap DuckDB at 2GB; a test
  keeps the cap at most half of `MemoryHigh`.
- Writers stopped rewriting unchanged rows and a nightly compaction bounds the
  file ([analytics](../analytics.md)).

## also found

`autofix` had failed on every run since 2026-10-30 01:47 UTC: the
openai-codex OAuth token on HeavyPad was revoked server-side while its stored
expiry still read valid, so pi never refreshed it. It was refreshed by forcing
expiry in a scratch HOME and installing the result.

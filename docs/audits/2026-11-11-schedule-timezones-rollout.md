# Schedule timezone rollout

HeavyPad API and services run server main revision
`78beba2e1647b58e33534f43795f00cd2106e3d1`. Verified November 11, 2026 at
20:33 UTC. The [receipt](2026-11-11-schedule-timezones-rollout.json) records
image pins, deployment generations and canaries.

## What changed

The server evaluated cron schedules in UTC and ignored `timezone`, ignored an
interval schedule's `anchor_date`, and stored schedules it could never run
(unknown timezone, rrule). Found when `lighting/home` was registered as
`0 1 * * *` in `America/Chicago` and scheduled for 01:00 UTC. The server now
evaluates schedules as upstream Prefect does and answers 422 for one it cannot
evaluate; its `docs/schedules.md` has the semantics. Zones come from alpine
`tzdata`, added to the runtime image.

The image also carries what landed on server main after the previous pin
`50aec7f`: the httpz, websocket and zat/zttp transport revisions. nate chose to
ship main as is rather than a timezone-only build.

## Evidence before rollout

- Server unit tests replay Python zoneinfo offsets and upstream Prefect 3.8.2
  run times; a one-off sweep matched Python on all 598 zones in tzdata 2026f
  (62,282 samples).
- Each server commit passed `zig build test` and `zig build` on its own.
- The backend matrix passed on sqlite and postgres with memory and redis.
- Pipeline `3n22rv7xfee22` passed CI, including the new
  `scripts/test-schedule-timezones`, and published the image.
- All 37 schedules on this server were cron in UTC or without a timezone, so
  none of them moved.

## Rollout

Configuration commit `585e7d6` was on GitHub and Tangled main before applying.
The baseline manifest diff was empty; the rollout diff changed only the image
pin and generation of each Deployment. API rolled first and returned
`0.0.31+78beba2e1647b58e33534f43795f00cd2106e3d1`, then services. API generation
21 and services generation 12 each have one updated, ready replica and zero
restarts. All background services reported startup. Postgres and Redis kept
their pods. The final manifest diff is empty.

The manual `prefect-home-canary/canary` deployment completed before
(`a5e5eb89-8835-4231-a62d-ec292b2f8941`) and after
(`1c8eb35e-fcc9-4c2b-aad3-600a0a8df69f`) in one attempt each. In the twelve
minutes around the rollout 10 runs completed and none failed or crashed.

## Proof in production

- `lighting/home` was re-registered at `1699719` as `0 1 * * *` in
  `America/Chicago`. Its three scheduled runs, created after registration, are
  at 07:00 UTC, which is 01:00 CST.
- A deployment create with timezone `Mars/Olympus` returned 422
  `Invalid timezone: "Mars/Olympus"` and stored nothing.

Daylight-saving behavior is not observable in production until March 14, 2027;
it rests on the upstream-parity tests.

## Rollback

Restore the previous digest recorded in the receipt in `stack.yaml` on main and
apply only the API and services Deployment documents through `just kubectl`.
`lighting/home` would then need its schedule back at `0 7 * * *` UTC.

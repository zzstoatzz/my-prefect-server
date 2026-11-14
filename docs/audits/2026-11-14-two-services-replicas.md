# HeavyPad API and services replicas

HeavyPad runs two API replicas and two services replicas. The original cluster
ran two API replicas and one services replica; the HeavyPad pilot was defined
with one of each on 2026-10-25 and the counts were not revisited until now.
Both tiers are on the single k3d node, so this covers a pod restarting or being
deleted, not the node, Postgres or Redis.

## What was checked before the API change

Server source: prefect-server `8c775d4f23edc25d3ea220eac5e98a1fdc8e1855`.

- Concurrency leases use Redis when `PREFECT_BROKER_BACKEND=redis`
  (`src/leases/storage.zig`); HeavyPad sets it.
- atproto login keeps no server-side session. The in-flight login and the
  session are sealed cookies (`docs/atproto-login.md`).
- The server's Docket runtime takes its storage from the broker setting
  (`src/docket_runtime.zig`), so it is on the shared Redis. Upstream Prefect
  needs `PREFECT_SERVER_DOCKET_URL` for this; this server has no such setting.
- `--webserver-only` pods start no Docket worker. Redis showed one consumer in
  the `docket-workers` group, the services pod, with nothing pending.
- Postgres had 16 of 100 connections in use with one API pod.

## API readback

`replicas: 2` was applied to the API Deployment alone; the live diff was the
replica count. Each API pod was then deleted in turn while
`https://prefect-server.waow.tech/api/health` was probed: 80 of 80 and 100 of
100 requests returned 200. Four workers logged dropped connections in the
second the original pod died and reconnected. All five pools stayed ready, the
41 flow runs of the following half hour completed, and no concurrency slots
were left held.

## Services qualification

Two services replicas had not been run in any earlier topology: the HA compose
stack, the chaos lab and the original cluster all used one. The automations
evaluator also keeps its event clock and each trigger's next-due time per
process, and both replicas join the Docket consumer group as
`docket-worker-1`.

The test ran on HeavyPad in a disposable compose project built from the chaos
lab's `compose.yaml` plus [an override](evidence/2026-11-14-two-services-replicas/override.yaml),
using the images production runs: API `8c775d4` and services
`sha256:4f82da38…` (image ID `672f69176850`). Each pass started from empty
volumes and [sent](evidence/2026-11-14-two-services-replicas/probe.py) 320
events at about 15 a second through three do-nothing automations, with a
20-second interval schedule running alongside.

| Pass | Events stored | Every event (200) | Five per resource (20) | Expected event absent (20) | Duplicate scheduled runs |
| --- | --- | --- | --- | --- | --- |
| one replica | 320 | 200 | 20 | 20 | 0 of 100 |
| two replicas | 320 | 200 | 20 | 20 | 0 of 100 |
| two, second killed mid-stream | 320 | 200 actions, 201 triggers | 20 | 20 | 0 of 100 |
| two, first killed mid-stream | 320 | 200 | 20 | 20 | 0 of 100 |

The Docket stream ended every pass with nothing pending, and neither services
container logged an error. [Raw results](evidence/2026-11-14-two-services-replicas/results.txt).

The extra trigger in the third pass was not investigated further. The likely
cause is that the killed replica emitted `prefect.automation.triggered` and
died before acknowledging the event, so the survivor processed it again; the
action ran 200 times. Delivery is at least once, as it is when a single
replica restarts.

Not covered: repeated passes, higher event rates, real notification actions,
and a kill timed inside a scheduler tick.

## Rollback

Set the replica count back to 1 in `stack.yaml`, land it, and apply that
Deployment alone. Neither change altered images, settings or storage.

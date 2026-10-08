# durable Redis migration

The real-client cutover rehearsal passed seeds 91 and 92 on 2026-10-08:
64 acknowledged events were delivered and persisted, the worker completed once
with each of 16 effects once, and API p95 was 11.77/10.76 ms with zero errors.
The original Redis storage preservation checks remain recorded below.

Production staging is now applied from `cbe2f39`: the 1 GiB PVC and durable
replica exist, replication is connected and synchronized, and AOF writes and
rewrite status are healthy. The replica uses the exact lab image, Redis 7.4.11.
The original primary is Redis 7.4.9. The service still selects `prefect-redis`;
no promotion or routing change has occurred. The production server remains
`993424a` with one API. The two-API rollout and promotion wait for the existing
prod-quiet gate: `email-triage-4d60aed8` is paused with a live process and
pause_reschedule=false. A concurrent transform completed. Do not cancel the
paused run without the user's answer to the pending question.

The production event canary has 64 acknowledged, delivered and persisted IDs
in HeavyPad's `~/.local/share/prefect-chaos/results/production-events-before-rollout.json`.
Verify those same IDs after the server rollout and Redis transition with the
server repository's `scripts/chaos/production-events.py verify`, using the
existing worker credential file. No new credential file was created.

The target is a single persistent Redis with synchronous AOF, no eviction, a
192 MiB dataset ceiling, and a 512 MiB container ceiling for rewrite headroom.
The PVC requests 1 GiB; local-path storage does not by itself enforce that as a
filesystem quota. Monitor actual disk usage. No new cloud VM is required.

The 2026-10-08 production observation was 81.5 MiB allocated Redis memory,
190.5 MiB historical peak, and approximately 94 MiB pod RSS. Node usage was
3390 MiB of approximately 7.56 GiB and 908m CPU. These are observations, not a
capacity guarantee. The chaos lab uses smaller limits with the same durability
and eviction policy.

## staged transition

The isolated rehearsal in `prefect-server/scripts/chaos/redis-migration.py`
(commit `045efe4`) used the same Redis image and synchronous AOF policy. After
coordinated failover and forced replacement of the new primary it verified all
96 stream entries byte-for-byte, 13 pending IDs with owners and delivery counts,
and a lock with its remaining expiry. The stronger repeat took 16.4 seconds,
including a 1.5-second observed failover, and removed both containers and its
volume. This proves storage preservation for that workload, not transparent
reconnection of Prefect clients through a Kubernetes Service change.

Rehearse with real acknowledged events and pending consumer entries first.
Deploy and verify the tested server image through the server repository's
`just prod-deploy <sha>` and `just prod-verify` recipes before promoting Redis.
Check the real work queue is quiet immediately before migration.

```bash
just redis-migrate prepare
just redis-migrate status
just redis-migrate promote <tested-full-server-sha>
just redis-migrate status
just redis-migrate route
```

Prepare creates the PVC and the new deployment without changing the existing
Service. It starts replication from the old pod. Promote requires both server
deployments to reference the supplied image SHA and requires a connected,
synchronized replica. Redis's coordinated `FAILOVER TO ... TIMEOUT 10000`
pauses writes, catches the replica up, then demotes the old primary before
promoting the new one. The command is asynchronous; a successful command alone
does not establish completion. No `FORCE` or automatic `ABORT` is used.

Route refuses until the old instance follows the new primary, coordinated
failover is complete, and AOF reports healthy writes with no rewrite underway.
It applies the final manifest to move the stable `prefect-redis` Service.
Existing sockets can briefly receive read-only errors and must reconnect;
the rehearsal must prove SDK recovery and preservation of acknowledged events.

After routing, verify endpoint addresses, actual SDK event delivery, persisted
event counts, worker throughput, and bounded latency. Retain the old replica
until these checks pass. Remove it only as a separate deliberate cleanup; it
is not part of the final manifest. Rolling back the Service alone is unsafe
because the old instance is now a replica. A reverse coordinated failover
requires checking both replication roles and which instance has current data.

Reference: [Redis coordinated failover](https://redis.io/docs/latest/commands/failover/).

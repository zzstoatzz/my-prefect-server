# durable Redis migration

The Redis storage transition has been rehearsed; application-client cutover and
the Kubernetes procedure have not yet been exercised. Production still uses the
volatile `prefect-redis` deployment. Do not apply the final Redis
manifest directly to an existing installation: that would route clients to an
empty database.

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

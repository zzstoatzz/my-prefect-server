# Isolated HeavyPad Prefect pilot

The pilot is a separate k3s cluster inside Docker (k3d), with its own PostgreSQL,
durable Redis, credentials, worker and deployment file. It uses the same immutable
server image as the current Hetzner instance. The original hostname, worker and
critical deployments remain on Hetzner. Never restore the production database
into a running pilot scheduler: copied deployments/automations could execute twice.

Kubernetes API binds to host loopback 16443; the application binds to loopback
14200. Tailscale Serve adds a distinct private HTTPS endpoint on port 10443.
Existing Serve/Funnel mappings are preserved. This requires Tailscale on clients;
it is not a public Funnel endpoint. Canonical-domain cutover is a later decision.

Source must be on main before applying. Run `cluster.sh` on HeavyPad after
installing checksum-verified k3d 5.9.0. Kubernetes is pinned to the current cloud
node's k3s release. Secrets come from the canonical encrypted store via stdin;
never commit rendered credentials or dump Kubernetes Secret objects. Persistent
volumes are under `~/.local/share/prefect-home/storage`. Do not delete the cluster
or its storage as an ordinary rollout operation.

Only low-risk flows qualify for this pilot. Verify a real manual run, persistent
state, backup/restore and authenticated remote access before transferring schedule
ownership. Pause the old schedule and cancel its queued scheduled runs before
activating the new one. Do not copy production alerting, Gardener, publishing,
email or Typeahead jobs as part of this goal. Migration records must identify the
old/new deployment IDs, source pin, schedule and rollback.

## Verified operating state

Live October 25, 2026 at https://heavypad.tailb660b6.ts.net:10443/.
Tailscale must be connected. Sign-in uses the pilot credential in the canonical
SOPS store (`heavypad_prefect`); the original server credential is deliberately
rejected here. Anonymous API access is rejected. `prefect-server.waow.tech`
still resolves to the original cloud node; no canonical routing was changed.

The only migrated existing deployment is `fastmcp-backlog/fastmcp-backlog`:
GitHub reads and small appends to the existing local counts series, with
`backfill_days=0`. It is active every 30 minutes on the pilot. The old server has
no backlog schedule and its three queued scheduled runs were cancelled. Its
history remains there. The first automatic backlog run completed at 00:00:02 UTC on October 26,
after two successful manual runs. The pilot canary is manual-only. All other original
workloads, automations, alerts and secrets remain on the original server. In
particular, the Typeahead publisher remains on its cloud pool.

- Pilot backlog deployment: `75c6a70d-2fcb-46c5-adc9-09522f6f2aea`.
- Original backlog deployment: `5089a7a2-d3ed-46c6-99bf-314948792a8b`.
- Pilot pool: `heavypad-pilot-pool`, concurrency one; worker
  `prefect-home-pilot-worker.service` is a separate user unit.
- `prefect.heavypad.yaml` owns pilot schedules and pins. The original
  `prefect.yaml` deliberately has `schedules: []` for backlog, so normal CI
  cannot restore its old recurring schedule.

Use the separate operations targets:

```sh
just heavypad-prefect version
just heavypad-prefect config view
just heavypad-prefect deployment inspect fastmcp-backlog/fastmcp-backlog -o json
just heavypad-kubectl -n prefect-home get pods
just heavypad-prefect-secrets
just heavypad-prefect-backup
```

Register only the pilot file with an actual committed source pin:

```sh
MPS_PIN="@$(git rev-parse HEAD)" just heavypad-prefect --no-prompt deploy --prefect-file prefect.heavypad.yaml --all
```

The pilot intentionally has a separate registration path; existing CI registers
only the original file. Pilot updates require the explicit command above. CLI
wrappers preserve argument boundaries and override endpoint/auth in the child
process without editing the default Prefect profile.

## Persistence and recovery

PostgreSQL uses a 20 GiB PVC; Redis uses a 1 GiB PVC with synchronous AOF and
no eviction. These are isolated from the original node. The server runs the
existing `8733e319` image by immutable digest. Application API and Kubernetes
API Docker port mappings bind only to 127.0.0.1. Existing Tailscale mappings on
443, 8443 and 9443 were verified unchanged. Port 10443 is private Serve, not Funnel.

The original UI tag no longer resolves in the registry. Its cached 14.4 MiB image
was exported read-only from the old node and imported into the pilot. The OCI
archive is retained at `~/.local/share/prefect-home/images/prefect-ui-3c108a1.tar`
on HeavyPad and copied to the operator Mac's private backup directory. Before
rebuilding this cluster, import that archive; do not assume the registry tag is
recoverable. Manifest digest:
`sha256:33a07cc4c10caf78af728e484447620c5c80b4d26e5a40b366cc7fc76cad95c3`.
The server image itself pulled successfully from the registry.

`prefect-home-backup.timer` creates an age-encrypted PostgreSQL custom-format dump
at 05:45 UTC daily with up to five minutes of jitter, retaining seven days.
Files live under `~/.local/state/prefect-home/backups`, separate from cluster
storage. Recipients are the canonical laptop/HeavyPad age keys. Manual encrypted
backups were copied to `~/.local/state/prefect-home-backups` on the operator Mac.
The initial dump was decrypted privately and restored to a separate scratch
PostgreSQL database; the completed canary record was verified, then the scratch
database was removed. A later dump includes the migrated flow and copied block.
Off-host copies currently require operator execution; automatic off-host backup
replication remains a prerequisite before migrating critical workloads.

The pilot API/services were restarted, then the whole isolated k3d cluster was
stopped and started. Database state and the canary survived; all pods and the
pilot worker recovered, and the migrated backlog flow subsequently completed.
Docker is enabled at boot, k3d containers use `unless-stopped`, and the user
manager has lingering enabled. This is still one host and one disk, not HA.

Credential consumers have an idempotent render/check command. A harmless comment
was injected into the pilot worker environment; the check detected drift and
`--apply` restored the canonical bytes. Auth rotation requires render plus pilot
API/services/worker restart. PostgreSQL rotation also requires updating the actual
database role; a Secret change alone cannot rotate an initialized role. No
existing credential was rotated. The only copied flow block is `github-token`.

## Rollback and later cutover

For backlog rollback, first deactivate its pilot schedule and cancel its queued
scheduled runs. Wait for any active pilot run to finish. Restore the old schedule
in the original source file and deactivate it in the pilot source file, then
register those selected deployments with their respective CLI targets. Verify
only one instance has queued recurring runs. Keep the local counts file; it is
shared persistent output, not an instance-owned disposable file.

Do not point the canonical hostname at this pilot yet. Before that separate
cutover: inventory every remaining client, pool, automation, cursor, limit,
Secret block and history dependency; plan PostgreSQL/Redis state ownership;
provide automatic off-host backups and a verified public/private ingress path;
keep shared DuckDB writers under one cross-instance lock or transfer them as a
cohort (each server has independent concurrency limits); move Hub, monitoring
and push notifications; decide cloud placement for the
Typeahead builder; and rehearse routing rollback. Never attach both schedulers
to a restored production database or activate copied automations by default.
The current Hetzner node remains required and billed until that work is complete.

Execution receipts are linked from
[the pilot audit](../../docs/audits/2026-10-25-heavypad-prefect-pilot.md).

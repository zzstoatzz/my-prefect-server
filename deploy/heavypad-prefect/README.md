# Isolated HeavyPad Prefect

The HeavyPad server is a separate k3s cluster inside Docker (k3d), with its own PostgreSQL,
durable Redis, credentials, worker and deployment file. It uses the same immutable
server image as the current Hetzner instance. The original hostname, worker and
critical deployments remain on Hetzner. Never restore the production database
into a running HeavyPad scheduler: copied deployments/automations could execute twice.

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

The initial pilot has expanded through the approved 21-deployment scoped migration
in batches 1, 2, 4, 5 and 8; see the [completion and rollback receipt](../../docs/migrations/2026-10-26-scoped-migration.md).
Verify real execution, persistent state, backup/restore and authenticated remote
access before transferring additional ownership. Pause the old schedule and cancel its queued scheduled runs before
activating the new one. Only individually qualified notification rules and
publication flows may move. Gardener, Typeahead and the long publishers remain deferred; email is included
in the completed scoped migration.
Migration records must identify the
old/new deployment IDs, source pin, schedule and rollback.

## Verified operating state

Live October 25, 2026 at https://heavypad.tailb660b6.ts.net:10443/.
Tailscale must be connected. Sign-in uses the HeavyPad server credential in the canonical
SOPS store (`heavypad_prefect`); the original server credential is deliberately
rejected here. Anonymous API access is rejected. `prefect-server.waow.tech`
still resolves to the original cloud node; no canonical routing was changed.

The first migrated deployment is `fastmcp-backlog/fastmcp-backlog`:
GitHub reads and small appends to the existing local counts series, with
`backfill_days=0`. It is active every 30 minutes on the HeavyPad server. The old server has
no backlog schedule and its three queued scheduled runs were cancelled. Its
history remains there. The first automatic backlog run completed at 00:00:02 UTC on October 26,
after two successful manual runs. Host diagnostics also runs here hourly at :37,
with its old schedule cleared. Its notification block and two notification rules
are copied and verified; automatic failure diagnosis remains on the old server.
Bufo traffic now publishes hourly at :07 here; its original schedule is cleared.
Its Cloud counterpart defaults to dry-run and has no schedule, but controlled
remote reads and real PDS publication have both passed with public readback.
Public MCP health checks run here hourly at :12. The unhealthy-finding rule is
copied and verified; the Cloud counterpart has passed the same eight-server
protocol/discovery sweep without notification automations. The broader
`fleet-health` job runs here at :03/:18/:33/:48 UTC. Its localhost bridge probe
retains the same HeavyPad host vantage; the original schedule is cleared.
Costs publish daily at 08:00 UTC here after real Cloud and HeavyPad publication
and public PDS readback; the old cost schedule is cleared. Cloud is manual-only
and defaults to dry-run. Known unmeasured Fly root disks and R2 storage remain
explicitly labelled `Incomplete`; this migration does not make estimates invoices.
The canary is manual-only. Other original workloads remain on the original
server; in particular, the Typeahead publisher remains on its cloud pool.
MCP Atlas and Bisk snapshot publication also moved on October 26. Their original
schedules are cleared, and HeavyPad owns `45 */6 * * *` and `*/10 * * * *` UTC
respectively. The Atlas flow now lives here at `flows/mcp_atlas/crawl.py`, with a
pinned Atlas library dependency; the Atlas product repo no longer owns a flow or
deployment file. Both publishers passed real HeavyPad execution and public
readback. Their automatic run receipts are recorded in the migration plan.
See the [migration and Cloud recovery plan](../../docs/migrations/heavypad-cloud-recovery.md)
for IDs, real run receipts, alerting limits and the complete downtime inventory.

- HeavyPad backlog deployment: `75c6a70d-2fcb-46c5-adc9-09522f6f2aea`.
- Original backlog deployment: `5089a7a2-d3ed-46c6-99bf-314948792a8b`.
- HeavyPad pool: `heavypad-pool`, concurrency one; worker
  `prefect-heavypad-worker.service` is a separate user unit.
- `prefect.heavypad.yaml` owns HeavyPad schedules and pins. The original
  `prefect.yaml` deliberately has `schedules: []` for migrated flows, so normal CI
  cannot restore its old recurring schedule.

An empty source schedule does not necessarily clear an existing live schedule.
Clear it explicitly, then assert the old schedule list is empty and there are
no queued/pending/running old runs **before** enabling the new owner. For example:

```sh
just prefect --no-prompt deployment schedule clear diagnostics/diagnostics --accept-yes
```

Use the separate operations targets:

```sh
just heavypad-prefect version
just heavypad-prefect config view
just heavypad-prefect deployment inspect fastmcp-backlog/fastmcp-backlog -o json
just heavypad-kubectl -n prefect-home get pods
just heavypad-prefect-secrets
just heavypad-prefect-backup
```

Register only the HeavyPad server file with an actual committed source pin:

```sh
MPS_PIN="@$(git rev-parse HEAD)" just heavypad-prefect --no-prompt deploy --prefect-file prefect.heavypad.yaml --all
```

The HeavyPad server intentionally has a separate registration path; existing CI registers
only the original file. HeavyPad updates require the explicit command above. CLI
wrappers preserve argument boundaries and override endpoint/auth in the child
process without editing the default Prefect profile.

## Persistence and recovery

PostgreSQL uses a 20 GiB PVC; Redis uses a 1 GiB PVC with synchronous AOF and
no eviction. These are isolated from the original node. The server runs the
existing `8733e319` image by immutable digest. Application API and Kubernetes
API Docker port mappings bind only to 127.0.0.1. Existing Tailscale mappings on
443, 8443 and 9443 were verified unchanged. Port 10443 is private Serve, not Funnel.

The original UI tag no longer resolves in the registry. Its cached 14.4 MiB image
was exported read-only from the old node and imported into the HeavyPad server. The OCI
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
Since October 26, encrypted dumps also replicate automatically to R2 (below).

### Off-site replication to R2

HeavyPad pushes the already age-encrypted dumps to the private R2 bucket named by
`heavypad_prefect.r2_backup_bucket` (planned: `heavypad-prefect-backups`), under
`postgres/`. R2 only ever holds ciphertext. A successful
`prefect-home-backup.service` starts `prefect-home-offsite.service` (`OnSuccess=`), and
`offsite.timer` repeats it daily at 17:45 UTC to catch up after a failed upload.
`offsite.sh` runs a digest-pinned rclone container with `copy --immutable --checksum`:
new dumps are uploaded under their unique timestamped names, an identical existing object
is skipped without a write (so re-runs succeed inside the lock window), an existing object
whose content differs fails the run instead of being overwritten, and nothing is deleted
when HeavyPad prunes its seven-day local copies. `rclone check --one-way` must then confirm
every local dump is in R2 before `offsite.json` records the newest upload.

A 90-day lifecycle rule expires objects. A bucket lock rule (7 days, if the bucket
supports it) blocks deletes and overwrites, including those made with the upload
credential. R2 has no "write but not delete" permission, so only the lock window is
protected against a stolen upload token; older copies are not. The token is scoped
to Object Read & Write on this one bucket. Its S3 credentials, endpoint and bucket name
live in the canonical store as `heavypad_prefect.r2_backup_access_key_id`,
`r2_backup_secret_access_key`, `r2_backup_endpoint` and `r2_backup_bucket`;
`just heavypad-prefect-secrets` renders `~/.config/prefect-home/r2.env` (mode 600) and
checks it by hash.

All three timers use `FixedRandomDelay=true`. Without it, a user-manager daemon-reload
re-rolls `RandomizedDelaySec`. The first scheduled 05:45 UTC backup on October 26 never
triggered while other units were being reloaded near its elapse time, and a timer that
has never run has no `Persistent=` stamp to catch up from.

`offsite-verify.sh` (weekly, `offsite-verify.timer`) downloads the newest R2 object,
verifies its hash against R2 and against the local dump if HeavyPad still has it,
decrypts it with HeavyPad's age key, and restores it into a throwaway PostgreSQL
with the server's pinned image digest. That container has `--network none`, a tmpfs data
directory and memory/CPU limits. It runs on HeavyPad's host Docker, outside k3d, and no Prefect
server is started, so restored schedules and automations cannot run. It asserts the
HeavyPad deployment IDs exist and the newest completed run is within two hours of the
dump time, then records counts (never values) in `restore-verifications.log` and
`restore-verified.json`. Update its deployment ID list when ownership changes.

The `backup-freshness` deployment reads those status files daily at 07:05 UTC and
fails if the newest local dump is over 26 hours old, R2's newest object differs from it,
the last upload is over 26 hours old, or the last verified restore is over eight days old.
A failed run is the alert, through the existing `flow-run failure -> discord` rule. That
alert depends on HeavyPad itself, so it does not detect a HeavyPad outage.

Enabled October 26, 2026 from `6929e03`: all ten dumps through
`postgres/20261026T074947Z.dump.age` uploaded and checked; the restore from R2 passed
(7 deployments, 74 flow runs, newest completed run 12 minutes before the dump); the
first manual `backup-freshness` run completed. The previous backup unit (without
`OnSuccess=`) and timer (without `FixedRandomDelay=`) are kept beside the scripts as
`*.pre-onsuccess` and `*.pre-fixed-delay` for rollback.

#### Enable runbook

Run in order from a clean main checkout; stop at the first failed check. Each step lists
its rollback. Prerequisite: the `heavypad_prefect.r2_backup_*` keys exist in the store.

1. Land source. Merge this work to main and `just push`. Rollback: revert the commits.
2. Render the R2 credential. `just heavypad-prefect-secrets --apply`, then the plain check
   must print "already current"; other consumers re-render byte-identically. Rollback:
   `ssh stoat@heavypad 'rm ~/.config/prefect-home/r2.env'`.
3. Fix the backup timer delay. Compare first, then install and reload:

   ```sh
   ssh stoat@heavypad 'cat ~/.config/systemd/user/prefect-home-backup.{service,timer}' | diff - <(cat deploy/heavypad-prefect/backup.{service,timer})
   scp deploy/heavypad-prefect/backup.timer stoat@heavypad:.config/systemd/user/prefect-home-backup.timer
   ssh stoat@heavypad 'systemctl --user daemon-reload && systemctl --user list-timers prefect-home-backup.timer'
   ```

   The next elapse must stay put across a second `daemon-reload`. Rollback: restore the
   previous timer file (without `FixedRandomDelay=`) and reload.
4. Install the scripts and off-site units, not yet chained or timed:

   ```sh
   scp deploy/heavypad-prefect/offsite{,-verify}.sh stoat@heavypad:.local/share/prefect-home/
   for unit in offsite.service offsite.timer offsite-verify.service offsite-verify.timer; do
     scp "deploy/heavypad-prefect/$unit" "stoat@heavypad:.config/systemd/user/prefect-home-$unit"
   done
   ssh stoat@heavypad 'systemctl --user daemon-reload'
   ```

   Rollback: remove those four unit files and two scripts, then reload.
5. First upload: `just heavypad-prefect-offsite`. Uploaded objects stay locked for seven days.
   Readback: `offsite.json` names the newest local dump. Rollback: none needed;
   objects expire through the lifecycle rule.
6. First restore check: `just heavypad-prefect-offsite-verify` prints an `ok` line. Rollback:
   none; it only reads R2 and removes its own container and files.
7. Chain uploads to backups and start the timers:

   ```sh
   scp deploy/heavypad-prefect/backup.service stoat@heavypad:.config/systemd/user/prefect-home-backup.service
   ssh stoat@heavypad 'systemctl --user daemon-reload && systemctl --user enable --now prefect-home-offsite.timer prefect-home-offsite-verify.timer'
   ```

   Rollback: `systemctl --user disable --now` both timers, restore the backup service
   without `OnSuccess=`, reload.
8. Register freshness inactive, then prove it:

   ```sh
   MPS_PIN="@$(git rev-parse HEAD)" just heavypad-prefect --no-prompt deploy --prefect-file prefect.heavypad.yaml --name backup-freshness
   just heavypad-prefect deployment run backup-freshness/backup-freshness --watch
   ```

   It must complete. Then set its schedule `active: true`, land that on main, and register
   it again with the new pin. Rollback: set `active: false` and register again, or
   `just heavypad-prefect deployment delete backup-freshness/backup-freshness`.

Locked objects remain until their lock expires; change the lock rule only by explicit decision.

The HeavyPad server API/services were restarted, then the whole isolated k3d cluster was
stopped and started. Database state and the canary survived; all pods and the
HeavyPad worker recovered, and the migrated backlog flow subsequently completed.
Docker is enabled at boot, k3d containers use `unless-stopped`, and the user
manager has lingering enabled. This is still one host and one disk, not HA.

After the October 26 host reboot, k3s in the running server container twice exited
within seconds ("failed to find interface with specified node ip"). Docker never
restarts the container for that, so the API stayed down until a manual restart.
`prefect-home-k3s-guard.service` (enabled for `default.target`) waits up to two
minutes for `/readyz` after each boot, restarts the server container at most twice,
and otherwise fails visibly in `systemctl --user --failed`. It does nothing when k3s
is healthy.

Credential consumers have an idempotent render/check command. A harmless comment
was injected into the HeavyPad worker environment; the check detected drift and
`--apply` restored the canonical bytes. Auth rotation requires render plus HeavyPad server
API/services/worker restart. PostgreSQL rotation also requires updating the actual
database role; a Secret change alone cannot rotate an initialized role. No
existing credential was rotated. Selected flow blocks are `github-token`,
`logfire-read-token` and `operator-atproto-creds`; the existing Discord notification
block is also copied. Only the latter two Secret blocks have Cloud recovery copies.
Use `just recovery-secret-blocks TARGET --block NAME` to verify those copies and
append `--apply` to repair drift. Their rotation path is in the canonical store
README and the migration plan.

## Rollback and later cutover

For backlog rollback, first deactivate its HeavyPad schedule and cancel its queued
scheduled runs. Wait for any active HeavyPad server run to finish. Restore the old schedule
in the original source file and deactivate it in the HeavyPad source file, then
register those selected deployments with their respective CLI targets. Verify
only one instance has queued recurring runs. Keep the local counts file; it is
shared persistent output, not an instance-owned disposable file.

Do not point the canonical hostname at this HeavyPad server yet. Before that separate
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
[the setup audit](../../docs/audits/2026-10-25-heavypad-prefect-pilot.md).

## Scoped worker capacity

The enabled `prefect-heavypad-worker@background`, `@interactive` and `@build`
instances use `worker@.service` plus the checked-in instance overrides.
Their pools are `heavypad-background-pool` (one slot/8 GiB),
`heavypad-interactive-pool` (two slots/2 GiB), and
`heavypad-build-pool` (one slot/32 GiB). Monitoring remains on its original
one-slot/2 GiB HeavyPad worker. The `presence` queue there has one slot and
can be paused independently for ingress cutover. Pools and worker limits must
match; a queue alone does not add capacity.

Dagster's two user services have `dagster-prefect.conf` drop-ins pointing at
the HeavyPad worker environment for the shared analytics writer lease. Change
that client only while both cohorts and Dagster are drained. Explicit command
Prefix `PREFECT_LOCAL_STORAGE_PATH` preserves the declared shared cache after
the Prefect worker settings merge; do not remove it as redundant job env.

Phone ingress uses the scoped backend rendered by
`just heavypad-presence-ingress --apply` and the Hub's private hostname alias.
Its public credential/URL are preserved. Gardener now uses the scoped HeavyPad
client environment and per-run tailnet transport described in the remaining
migration receipt. The normal
`just presence-deploy` registers the active HeavyPad definition. Stream's
spindle routing is checked/repaired by `scripts/stream_spindle_prefect.py`;
its canonical references are in the existing secret store.


## Registering main revisions

Spindle microVMs intentionally block private networks, including Tailscale IPs
and MagicDNS answers. CI validates source without Prefect credentials. Deployment
registration runs on the trusted HeavyPad host, using its existing
`~/.config/prefect-home/client.env` derived from canonical SOPS.

`prefect-home-registration.timer` checks every five minutes. Its oneshot service
fetches GitHub main into a dedicated clean clone, requires the matching Tangled
`deploy.yml` push pipeline to be green, verifies the existing HeavyPad process
pool, then validates and registers `prefect.heavypad.yaml` with the exact
`MPS_PIN=@<SHA>`. It records success in
`~/.local/share/prefect-home/registration/registered-sha`. A lock prevents
concurrent registration; the same successful SHA is a no-op. Failed or pending
CI leaves the previous deployed revision in place. Keep GitHub and Tangled main
in sync with `just push`.

Reconcile or inspect from an operator checkout:

```sh
ssh stoat@heavypad 'systemctl --user start prefect-home-registration.service'
ssh stoat@heavypad 'journalctl --user -u prefect-home-registration.service -n 12 --no-pager'
ssh stoat@heavypad 'systemctl --user list-timers prefect-home-registration.timer'
```

For an intentional manual rollback, stop the registration timer first so it
does not restore main's declarations. Register only the intended rollback
revision against the verified target, keep one scheduler owner, then reconcile
main before re-enabling the timer. Runtime state and secrets are not restored
by checking out source.

Source and units are `scripts/register_heavypad_main.py`,
`deploy/heavypad-prefect/prefect-home-registration.service` and its `.timer`.
The installed script is `~/.local/share/prefect-home/register-main.py`; install
updated source there after landing it on main, then reload the user manager when
units change. The oneshot reads freshly rendered credentials on every start.

Current migration, qualification, backup and canonical routing gates are in
[the remaining migration receipt](../../docs/migrations/2026-10-27-remaining-cutover.md)
and its linked live audit. Canonical routing and Hetzner retirement remain
separate work.

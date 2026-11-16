# HeavyPad outage runbook

What to do when HeavyPad is down and not coming back soon, and how to rebuild it
on new hardware. The [cloud recovery and downtime inventory](migrations/heavypad-cloud-recovery.md)
has the per-deployment consequences; this page is the operator's checklist.

## What is on HeavyPad

The inventory below comes from the November 8 recovery drill, with this
repository's worker ownership refreshed by the November 15 audit. Services
running on HeavyPad stop with it. Check other repositories' current ownership
before assuming a remotely hosted service has no home dependency.

| Service | Reached at | Source | State and where it is backed up |
| --- | --- | --- | --- |
| Prefect server (k3d: api, postgres, redis, services) and five process workers | Tailscale Serve, `deploy/targets.toml` | [`deploy/heavypad-prefect`](../deploy/heavypad-prefect/README.md) | PostgreSQL dump to R2 `postgres/` daily, restore-verified weekly, via [prefect-dr](https://github.com/prefectlabs/prefect-dr). Redis AOF, cluster manifests and secrets, worker env in the host snapshot. |
| spindle CI | spindle.zzstoatzz.io (cloudflared), Serve :443 | `zzstoatzz.io/tangled-infra` (laptop only, no remote) | **Not backed up.** `/etc/spindle` and `/var/lib/spindle/spindle.db` are root/spindle-owned. Repo CI secrets must be re-added on a new spindle. |
| cloudflared tunnel | home.zzstoatzz.io, login.zzstoatzz.io, docs-search.zzstoatzz.io, hub.waow.tech, spindle.zzstoatzz.io | `zzstoatzz.io/home` `deploy/cloudflared.yml` | Tunnel credentials in the host snapshot. DNS stays pointed at the tunnel. |
| home-mcp, atlogin | home.zzstoatzz.io, login.zzstoatzz.io | `zzstoatzz.io/home`, `zzstoatzz.io/atlogin` | Env files and atlogin state dir in the host snapshot. |
| lance-search, lance-docs, mcp-atlas search | docs-search.zzstoatzz.io, tailnet | `zzstoatzz.io/lance-search`, `zzstoatzz.io/mcp-atlas` | Indexes (about 2 GB) are rebuilt, not backed up. Env in the host snapshot. |
| prefect-edge (hub, grafana, prometheus, proxy, push), dagster hub | hub.waow.tech, tailnet | this repo, `deploy/prefect-edge`, `deploy/dagster` | Unit/env configuration is covered by the host snapshot. Their persistent directories under `.local/share/prefect-edge` and `.local/share/dagster-hub` are **not included** by the committed backup script. The Grafana timer only copies analytics locally. |
| gardener exe worker (disabled), pull-comment bridge, phi inference, bandwidth watchdog | Funnel :8443 (inference gateway) | this repo, `deploy/*` | `.local/state` and `phi-inference/state` are included in the host snapshot. Do not enable the retired exe worker during recovery. |
| inbox-zero, protonmail bridge, hydroxide | none | this repo, `deploy/inbox-zero` | Mailbox state and bridge credentials in the host snapshot. |
| talky-jev and its Slack bridge, remind | none | `zzstoatzz.io/talky-jev`, `waow.tech/remind` | State and env in the host snapshot. |
| albedo phone | Serve :11443 | albedo; `sandbox.sh` in the host snapshot | none |
| heavypad studio | Serve :9443 | **no repository**; app source is only in the host snapshot | queue state in the host snapshot |
| heavypad-llama (Gemma) | tailnet | this repo, `deploy/heavypad-llama` | model file is re-downloaded |

Prefect deployment records live in the PostgreSQL dump. This repository's
declarations and source pins are in `prefect.heavypad.yaml`; externally owned
deployments such as evergreen-timeline must be reconciled with their own repository.

The backup's scope is the explicit path list and exclusions in
[host-backup.sh](../deploy/heavypad-prefect/host-backup.sh), plus existing
environment files referenced by user service units. It is not a whole-host
backup. In particular, Prefect result/storage directories, installed tools and
registration receipts are excluded. The freshness check proves recent objects
exist; it does not prove that every service's state is covered. Recovering the
Prefect database does not recover Dagster metadata, push state or monitoring
history. Those gaps need a separate backup/recovery change.

Monitoring on HeavyPad itself goes dark with it. The external check is the
`heavypad-watchdog` Cloudflare Worker (`deploy/heavypad-watchdog`): every 15 minutes
it probes login.zzstoatzz.io, spindle.zzstoatzz.io and hub.waow.tech and reads the
age of the newest dump and host snapshot in R2. It posts to the Discord alerts
webhook when something fails, again daily while it stays failed, and once on
recovery. https://heavypad-watchdog.n8-3e9.workers.dev shows its current state.

## First hours

1. Confirm it is HeavyPad and not the home network: the watchdog has posted, `tailscale status` shows it
   offline; the public hostnames return 502 or time out.
2. Use the service inventory above and the deadlines below to assess affected
   consumers and decide whether to wait or fail over.
3. Deadlines, from the downtime inventory:
   - bufo-traffic backfills two days; older gaps are permanent.
   - paused email-triage approvals expire; do not replay them.
   - phi passes with a lock time (chicken) cannot be run late.
   - watch-tangled-pulls and merge-approved stop; open PRs wait. Autofix proposing
     is deliberately paused and must remain paused during recovery.
4. Manual Cloud recovery deployments exist for bufo-traffic, mcp-fleet-health,
   diagnostics and costs; commands and qualification receipts are in the cloud
   recovery doc. Costs retains known `Incomplete` coverage and is not a full
   production replacement. Before running a
   Cloud publisher, make sure HeavyPad cannot come back and run the same job:
   if it reboots, stop `prefect-heavypad-worker*.service` first.
5. CI and deploys for repos pinned to spindle.zzstoatzz.io stop, including this
   repo's main registration. To ship anyway, point the repo at the hosted spindle
   in its tangled settings and re-add its CI secrets from the secrets store.

## Rebuild on new hardware

Root of trust: the laptop's age key (`~/.config/sops/age/keys.txt`) and HeavyPad's,
both recipients of the host backup. HeavyPad's key is inside the host snapshot;
the laptop key decrypted both backup types in the drill below. It recovers the
encrypted payloads, not excluded service state. Keep an offline copy of the key.

1. Install the OS, Tailscale (join as `heavypad`), Docker and k3d. Restore
   `tailscale serve` from `captured/tailscale-serve.json` in the snapshot.
2. Fetch the newest objects from R2 bucket `heavypad-prefect-backups`: one
   `host/*.host.tar.age` and one `postgres/*.dump.age`. Credentials are
   `heavypad_prefect.r2_backup_*` in the secrets store. Decrypt with age.
3. Unpack the host snapshot into the new home. It restores `.config` (unit files
   and env files), `.cloudflared`, `.local/state`, the UI image archive, `secrets`
   and the laptop ssh-key backup. Use `captured/enabled-user-units.txt` as evidence
   of the captured state; reconcile it with current ownership and deliberate
   pauses before enabling units.
4. Prefect: follow the [server runbook](../deploy/heavypad-prefect/README.md).
   Import `prefect-ui-3c108a1.tar`, create the cluster, apply the secrets from
   `captured/prefect-home-k8s.yaml` (`atcr-creds` is the registry login for the
   server image, which is amd64 only), restore the dump into the new PostgreSQL and
   start Redis empty. Redis only holds in-flight state (event, log and action streams
   with their consumer groups, docket's service bookkeeping, action dedup keys, any
   concurrency leases); replaying an AOF from a different instant than the dump would
   re-fire actions and hold leases for runs that no longer exist. The snapshot's
   `captured/redis-data.tar` is for forensics on what was in flight, not for restoring.
   The server wants a plain `postgres://` URL. Start `--webserver-only` first and
   check deployments, automations, pools and blocks through the API; start the
   `services` deployment and the workers only once env files are in place, since
   services run schedules and automations the moment they start.
5. Recreate services from each owning repository's runbook and published revision.
   For this repo, use `just edge-install REVISION` for
   [edge service definitions](../deploy/prefect-edge/) and the
   [Dagster installation procedure](../deploy/dagster/README.md). Check state
   coverage above before starting a service with an empty data directory.
6. Start `home-mcp-tunnel.service` after its origins are ready. Verify each
   configured hostname against the restored tunnel configuration and DNS.
7. spindle: reinstall from `tangled-infra`, then re-add CI secrets per repo.
8. Verify: `backup-freshness`, `mcp-fleet-health` and `evergreen-timeline` runs
   complete; home.zzstoatzz.io answers; albedo loads.

## Rehearsals

2026-11-08, on the laptop with orbstack and only the laptop age key: fetched the
newest host snapshot and dump from R2, restored into the pinned postgres and redis
images on an internal docker network, started the pinned server webserver-only, and
read back 53 deployments (34 with active schedules), 29 automations, 9 work pools
and 49 blocks. Redis loaded its AOF. Nothing touched HeavyPad. Found and fixed: env
files were stored twice in the snapshot and bsdtar dropped them on extraction.

## Drill

Monthly, from the laptop, list the newest snapshot and prove it decrypts:

```sh
rclone lsf r2:heavypad-prefect-backups/host | tail -1
rclone cat r2:heavypad-prefect-backups/host/<name> | age -d -i ~/.config/sops/age/keys.txt | tar -tzf - | wc -l
```

`backup-freshness` fails when the snapshot is older than 26 hours, the dump is
older than 26 hours, or the last verified restore is older than eight days.

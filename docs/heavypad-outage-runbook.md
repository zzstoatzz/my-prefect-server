# HeavyPad outage runbook

What to do when HeavyPad is down and not coming back soon, and how to rebuild it
on new hardware. The [cloud recovery and downtime inventory](migrations/heavypad-cloud-recovery.md)
has the per-deployment consequences; this page is the operator's checklist.

## What is on HeavyPad

Everything below stops when HeavyPad does. Identity (PDS on Fly), stream, relay,
zeldi, plyr.fm and the static sites do not depend on it.

| Service | Reached at | Source | State and where it is backed up |
| --- | --- | --- | --- |
| Prefect server (k3d: api, postgres, redis, services) and six workers | Tailscale Serve, `deploy/targets.toml` | [`deploy/heavypad-prefect`](../deploy/heavypad-prefect/README.md) | PostgreSQL dump to R2 `postgres/` daily, restore-verified weekly. Redis AOF, cluster manifests and secrets, worker env in the host snapshot. |
| spindle CI | spindle.zzstoatzz.io (cloudflared), Serve :443 | `zzstoatzz.io/tangled-infra` (laptop only, no remote) | **Not backed up.** `/etc/spindle` and `/var/lib/spindle/spindle.db` are root/spindle-owned. Repo CI secrets must be re-added on a new spindle. |
| cloudflared tunnel | home.zzstoatzz.io, login.zzstoatzz.io, docs-search.zzstoatzz.io, hub.waow.tech, spindle.zzstoatzz.io | `zzstoatzz.io/home` `deploy/cloudflared.yml` | Tunnel credentials in the host snapshot. DNS stays pointed at the tunnel. |
| home-mcp, atlogin | home.zzstoatzz.io, login.zzstoatzz.io | `zzstoatzz.io/home`, `zzstoatzz.io/atlogin` | Env files and atlogin state dir in the host snapshot. |
| lance-search, lance-docs, mcp-atlas search | docs-search.zzstoatzz.io, tailnet | `zzstoatzz.io/lance-search`, `zzstoatzz.io/mcp-atlas` | Indexes (about 2 GB) are rebuilt, not backed up. Env in the host snapshot. |
| prefect-edge (hub, grafana, prometheus, proxy, push, health), dagster hub | hub.waow.tech, tailnet | this repo, `deploy/prefect-edge`, `deploy/dagster` | Config in the host snapshot. Grafana snapshots go to R2 via `prefect-grafana-snapshot.timer`. |
| gardener exe worker, hcloud builder, pull-comment bridge, phi inference, bandwidth watchdog | Funnel :8443 (inference gateway) | this repo, `deploy/*` release recipes | Cursors, `grants.sqlite`, watchdog state in the host snapshot. |
| inbox-zero, protonmail bridge, hydroxide | none | this repo, `deploy/inbox-zero` | Mailbox state and bridge credentials in the host snapshot. |
| talky-jev and its Slack bridge, remind | none | `zzstoatzz.io/talky-jev`, `waow.tech/remind` | State and env in the host snapshot. |
| albedo phone | Serve :11443 | albedo; `sandbox.sh` in the host snapshot | none |
| heavypad studio | Serve :9443 | **no repository**; app source is only in the host snapshot | queue state in the host snapshot |
| heavypad-llama (Gemma) | tailnet | this repo, `deploy/heavypad-llama` | model file is re-downloaded |

Prefect deployments (evergreen-timeline, atproto-docs, typeahead enrichment, the
phi passes, autofix, backup-freshness and the rest) live in the PostgreSQL dump
with their source pinned by `prefect.heavypad.yaml`.

Monitoring lives on HeavyPad too. Silence from Discord during an outage is the
outage, not health. There is no external check yet.

## First hours

1. Confirm it is HeavyPad and not the home network: `tailscale status` shows it
   offline; the public hostnames return 502 or time out.
2. Nothing user-facing is at risk except those hostnames and the inference gateway.
   Decide whether to wait or fail over using the deadlines below.
3. Deadlines, from the downtime inventory:
   - typeahead-index publication freshness fails after four days.
   - bufo-traffic backfills two days; older gaps are permanent.
   - paused email-triage approvals expire; do not replay them.
   - phi passes with a lock time (chicken) cannot be run late.
   - autofix, watch-tangled-pulls and merge-approved stop; open PRs wait.
4. Qualified manual Cloud recovery exists for bufo-traffic, mcp-fleet-health and
   diagnostics only; commands are in the cloud recovery doc. Before running a
   Cloud publisher, make sure HeavyPad cannot come back and run the same job:
   if it reboots, stop `prefect-heavypad-worker*.service` first.
5. CI and deploys for repos pinned to spindle.zzstoatzz.io stop, including this
   repo's main registration. To ship anyway, point the repo at the hosted spindle
   in its tangled settings and re-add its CI secrets from the secrets store.

## Rebuild on new hardware

Root of trust: the laptop's age key (`~/.config/sops/age/keys.txt`) and HeavyPad's,
both recipients of every backup. HeavyPad's key is inside the host snapshot, so the
laptop key alone recovers everything. Keep an offline copy of the laptop key.

1. Install the OS, Tailscale (join as `heavypad`), Docker and k3d. Restore
   `tailscale serve` from `captured/tailscale-serve.json` in the snapshot.
2. Fetch the newest objects from R2 bucket `heavypad-prefect-backups`: one
   `host/*.host.tar.age` and one `postgres/*.dump.age`. Credentials are
   `heavypad_prefect.r2_backup_*` in the secrets store. Decrypt with age.
3. Unpack the host snapshot into the new home. It restores `.config` (unit files
   and env files), `.cloudflared`, `.local/state`, the UI image archive, `secrets`
   and the laptop ssh-key backup. `captured/enabled-user-units.txt` is the list to
   `systemctl --user enable`.
4. Prefect: follow the [server runbook](../deploy/heavypad-prefect/README.md).
   Import `prefect-ui-3c108a1.tar`, create the cluster, apply the secrets from
   `captured/prefect-home-k8s.yaml`, restore the dump into the new PostgreSQL and
   unpack `captured/redis-data.tar` into the Redis PVC before starting the api.
   Pause automations until worker env files are in place, then start workers.
5. Clone each repository in the table and run its deploy recipe; the `*/current`
   release dirs are produced by this repo's `just deploy-*` recipes.
6. Start `home-mcp-tunnel.service`; the five hostnames return with no DNS change.
7. spindle: reinstall from `tangled-infra`, then re-add CI secrets per repo.
8. Verify: `backup-freshness`, `mcp-fleet-health` and `evergreen-timeline` runs
   complete; home.zzstoatzz.io answers; albedo loads.

## Drill

Monthly, from the laptop, list the newest snapshot and prove it decrypts:

```sh
rclone lsf r2:heavypad-prefect-backups/host | tail -1
rclone cat r2:heavypad-prefect-backups/host/<name> | age -d -i ~/.config/sops/age/keys.txt | tar -tzf - | wc -l
```

`backup-freshness` fails when the snapshot is older than 26 hours, the dump is
older than 26 hours, or the last verified restore is older than eight days.

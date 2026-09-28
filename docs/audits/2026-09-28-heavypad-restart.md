# Heavypad restart assessment — 2026-09-28

## Completed reboot

At the user's request, paused home-pool and phi-sprites-spike, confirmed no
active Prefect work, and stopped the idle Spindle runner. Rebooted remotely
over Tailscale. The host returned at about 10:25 CDT with boot ID
`c0bbd26b-f0a9-4d72-9753-0efe97622006` (previously
`b540a032-55f7-461d-a830-1280fa564605`). No physical intervention was needed.

- NVIDIA now reports RTX 4070 Laptop, 8188 MiB, driver 580.178.04.
- No failed system or user units; all six application user services active.
- Proton IMAP authenticated and answered NOOP successfully after cold boot.
- Pull-comment bridge reconnected with the same durable cursor,
  1790602593718883.
- Home Cloudflare tunnel ready with four connections; local MCP endpoint
  responds (GET returns 405, as expected for its configured transport).
- Spindle, Tailscale, node exporter and the Prefect worker started at boot.
- Both paused pools were resumed and reported READY.
- Post-boot watch-fastmcp run `48314051-cbc2-4e42-b453-e892a6ec11e5`
  completed successfully at 10:27:11 CDT.

The assessment below is the pre-reboot planning record. Driver update policy
and CUDA inference setup remain separate follow-ups.

Initial read-only assessment around 08:28–08:35 America/Chicago (CDT, UTC−5).
At the user's subsequent request, the pull-comment bridge was fixed and deployed
at 08:41; its service restart and cursor recovery were verified. No machine
reboot, schedule, pool, credential, or system-package changes were made.

## Recommended window

Monday September 28, **10:30–10:55 a.m. CDT**, with a live drain check before
shutdown. Reserve the whole 25 minutes for shutdown, startup, and validation;
this is a maintenance allowance, not a measured boot-time guarantee. An
alternative is Tuesday September 29 at the same time, after rechecking runs.

There is no empty schedule: watch-fastmcp runs every five minutes,
bisk-snapshot every ten, and hub-data-sync runs from cron every three.
Pause dispatch to home-pool and phi-sprites-spike, allow active work to finish,
then reboot. Do not stop the worker to drain it: its systemd unit uses
KillMode=control-group and a 90-second stop timeout.

The current typeahead-index run must finish first:
`2cd0a973-62ae-4ba6-9107-63667c125441`, started 08:12:28 CDT. This follows
today's 04:00 scheduled failure. Recent successful rebuilds took 69.5–72.6
minutes, suggesting about 09:22–09:25 completion if this run behaves similarly;
completion and publication must be checked, not assumed from elapsed time.

## Schedule evidence

Live production deployment schedules and recent run start/end times were read
through the repository's authenticated API configuration. The Prefect MCP
connection required reauthentication. All times below are Chicago time on the
assessment date; most source cron expressions use UTC and shift locally at DST.

| Work | Schedule / measured duration | Restart implication |
| --- | --- | --- |
| Ingest → classify → transform → brief/memory | Hourly at :00; transform ~7–8 min, downstream memory sometimes another ~8 min | Avoid the first ~20 min of an hour |
| Identity enrichment and strata-hourly | :20 and :21; recent strata up to ~6 min | Check these have finished before a :30 drain |
| Pub-search snapshot | Odd local hours at :40; usually ~13–14 min, recent maximum ~21 min | 09:40 precedes the proposed window; no 10:40 snapshot |
| Phi editorial | 10:00 daily; recent runs seconds | Check completion with the hourly pipeline |
| Typeahead enrichment backfill | 15:00 daily; recent successful runs 210 min | Avoid 15:00–18:30 and any retry afterward |
| Typeahead full index | 04:00 on cron `0 9 */3 * *`; next scheduled Oct 1 | Today's manual/recovery run overrides the nominal quiet period |
| PLC identity | Monday 00:00; today completed in 24.8 min | Already finished |
| Watchers, health, small snapshots | Every 5/10/15 min; diagnostics :37; pull reconciliation :17 | Expect delayed work during maintenance |
| On-demand agents, admission gates, CI, presence | Event/manual driven | A calendar window never substitutes for a live activity check |

The upcoming scheduled-run query has a bounded scheduling horizon per
deployment; missing distant five-minute entries do not mean those jobs stop.

## Impact and boot recovery

| Component | Impact while offline | Observed recovery configuration / validation |
| --- | --- | --- |
| Prefect home worker | Home-pool execution stops; queued work is delayed | Enabled system service; health on 127.0.0.1:8080; validate a completed real run after resuming |
| Sprite worker and Phi inference gateway | Dispatch pauses; remote Sprite jobs lose their inference route even though compute is elsewhere | Both enabled user services; drain phi-sprites-spike too; gateway on 8999 behind Tailscale 8443 |
| Spindle CI | Builds cannot execute; an in-flight microVM would be interrupted | Enabled system service; no firecracker/qemu process observed; recheck runner activity immediately before shutdown |
| Home MCP and Cloudflare tunnel | Home page and remote Hue/Fire TV controls unavailable; presence-triggered actions may be delayed | Both enabled user services; local app on 8765; validate through the public tunnel with a read-only operation |
| Proton Mail Bridge | Local IMAP/SMTP and dependent ingestion unavailable | Enabled user service; ports 1143/1025; cold-boot credential access is not proven by an active unit—validate authenticated mailbox access |
| Pull-comment bridge | Immediate revision requests delayed | Enabled user service, health on 8791; durable cursor at ~/.local/state/pull-comment-bridge/cursor, preserved through an actual service restart at 08:41 |
| Hub data sync | Dashboard data becomes temporarily stale | User crontab every three minutes; verify a successful sync after boot |
| Metrics, Tailscale, SSH | Temporary visibility and management gap | Enabled system services; Wi-Fi and wired autoconnect configured; verify remote access and metrics |

All six application user services are enabled and `Linger=yes` for stoat,
so a desktop login should not be required to start them. No failed system or
user units were present. There were no active Docker containers, tmux sessions,
or deferred at jobs observed. Root had no crontab. Docker's default user context
points at a dead Desktop socket; the actual system socket was checked separately.

The old prefect-server and prefect-worker system units are disabled. All three
old Docker containers have restart policy `no`; leave them stopped. The older
hydroxide user service is also disabled. No live local model server was found.

Pull-comment recovery was repaired before maintenance. The bridge now saves
an initial replay timestamp before connecting, fsyncs checkpoints, retains its
previous reconnect cursor if a write fails, and rejects corrupt state. A live
stream capture/reconnect replayed the identical event. Both relevant comments
found by the PDS reconciliation read were already marked handled; no manual
revision run or comment was created for verification. The deployed bridge's
cursor remained 1790602593718883 through a second service restart.

The hourly watch-tangled-pulls flow remains the fallback for history beyond
Stream's 36-hour replay window. It reconciles reviewer PDS records against
handled markers and ignores comments younger than ten minutes. After recovery,
verify reconciliation covers the outage; allow that grace period before a
manual catch-up. A successful health response alone does not prove event delivery.

## Execution checklist for the chosen window

1. Re-read active/pending/cancelling runs for both affected pools, check
   typeahead's successful publication, and inspect Spindle and host processes.
   Avoid new pushes/manual jobs during the drain. Defer if long work remains.
2. Record existing pool pause states and active run IDs. Pause dispatch to
   home-pool and phi-sprites-spike; verify no new submissions and wait for
   active/prefetched runs and CI to finish. Keep the workers alive while draining.
3. Record baseline service states, bridge status, and snapshot freshness. Verify
   no apt/dpkg operation is active. Preserve durable model files, cursors,
   grants database, and build evidence. Ensure someone can reach the machine
   physically if network or boot recovery fails.
4. Perform a normal system reboot. Keep pools paused through initial validation.
5. Verify Tailscale/SSH, networking, disk mounts, system and user failed units,
   and all application services. Check the worker health endpoint, authenticated
   Proton access, home tunnel, bridge subscription/reconciliation, Sprite gateway,
   CI listener, metrics, and hub sync. Restart only components that need it.
6. Run nvidia-smi. Installed DKMS is NVIDIA 580.178.04 for the current
   6.16.3-76061603-generic kernel; loaded module before reboot is 580.173.02
   while NVML is 580.178. The reboot should align these, but verify the result.
7. Restore the recorded pool pause states. Inspect delayed runs and downstream
   triggers; confirm actual successful runs, not only worker heartbeats. If any
   run was interrupted, reconcile its external effects before retrying it.
8. Review post-boot errors and resource use. Treat durable NVIDIA package-source
   alignment and update/reboot policy as a separate follow-up; reboot alone does
   not prevent the mismatch recurring on the next driver update.

Root currently has ~697 GiB free (60% used). The second ~931 GiB filesystem
is still unmounted and is not required to bring existing services back. Broad
cache deletion, mounting that disk, and installing inference software are not
required for this restart. First establish a healthy post-boot baseline.

## Evidence sources

- Production `/deployments/filter`, `/flow_runs/filter`, `/work_pools/filter`;
  allowlisted fields only, no credentials or deployment environment values.
- Live SSH: system/user units, enabled states, timers, cron, process inventory,
  listeners, Docker system socket, mounts, NetworkManager autoconnect, DKMS,
  worker and bridge health endpoints.
- `deploy/home-worker/prefect-home-worker.service`;
  `packages/mps/src/mps/pull_comment_bridge.py`, `flows/watch_tangled_pulls.py`,
  and Sprite/inference service source for recovery dependencies.

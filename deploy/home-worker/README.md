# home-worker

systemd unit for the Prefect **process** worker running on the home box (`heavypad`),
polling the prod server (`prefect-server.waow.tech`) over Tailscale for the `home-pool`
work pool. Outbound-only — no inbound ports, no public exposure.

## install (on heavypad, needs root)

```sh
sudo bash install.sh
```

Prereqs (already in place):
- `uv` at `/home/stoat/.local/bin/uv`
- `/home/stoat/.config/prod-worker/env` (mode 0600) with:
  ```
  PREFECT_API_URL=https://prefect-server.waow.tech/api
  PREFECT_API_AUTH_STRING=<user:pass>
  ```
- the `home-pool` work pool exists on the prod server.

## operate

```sh
systemctl status prefect-home-worker
journalctl -u prefect-home-worker -f
sudo systemctl restart prefect-home-worker
```

## notes
- `prefect-home-worker` now runs `/usr/local/bin/prefect-worker-guard`, which
  babysits the real `prefect worker start ...` command in
  `/etc/prefect-worker-guard.env`.
- `prefect` is pinned to the server version (3.7.2) to avoid client/server skew.
- The unit sets `PATH` to include `~/.local/bin` because the process worker spawns
  `uv run …` for each flow run.
- The unit is cgroup-limited (`MemoryHigh=12G`, `MemoryMax=18G`, `TasksMax=800`)
  and `KillMode=control-group` so leaked flow-run children cannot consume the
  whole laptop. `Restart=always` brings the guard back if systemd kills it.
- The guard checks the unit's systemd counters every 30 seconds and cycles the
  worker process group if memory exceeds 12 GiB or task count exceeds 500.
  Those are watchdog thresholds, not capacity targets: normal operation should
  remain in the low single-digit GiB range with a few hundred tasks. Crossing
  either threshold means the process worker is probably leaking descendants or
  wedged, and should be restarted before it reaches the old emergency shape.
- The guard also asks the Prefect API whether `heavypad` is still `ONLINE`.
  Three consecutive offline checks cycle the local worker process group. Ten
  consecutive API check failures do the same, which gives transient server or
  network blips room to clear without leaving the worker wedged forever.
- Retargeting a deployment onto `home-pool`: on the current (3.7.2) server, changing a
  deployment's work pool via `prefect deploy` leaves the old `work_queue_id` — delete and
  recreate the deployment so it binds to `home-pool`'s queue.

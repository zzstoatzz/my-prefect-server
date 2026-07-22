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
  and `KillMode=control-group` so a genuinely runaway service cannot consume the
  whole laptop. Those systemd hard limits are the final machine safety rail;
  the guard only observes the counters and never kills work based on them.
- The worker starts with Prefect's local `--with-healthcheck` endpoint enabled.
  The guard checks `http://127.0.0.1:8080/health` and replaces only the worker's
  control-process lineage
  after three consecutive failures past startup grace. This catches the
  dead-but-running case where the OS process is alive but the worker has stopped
  successfully polling for runs.
- The guard also scans the local systemd journal for worker-channel heartbeat
  failures from the current worker process generation. If the worker is wedged,
  the guard replaces the worker wrapper and scheduler processes while excluding
  every descendant carrying `PREFECT__FLOW_RUN_ID`. Active flow-run children
  remain alive and continue talking to the Prefect API independently.
- The guard does not poll the Prefect API for worker liveness. It uses Prefect's
  local worker health endpoint for that. It does make a bounded API read for any
  local descendant in the whole systemd unit that advertises
  `PREFECT__FLOW_RUN_ID`; if the server already says that run is terminal, the
  guard terminates only those descendant PIDs. Scanning the unit instead of the
  current worker process group also cleans terminal descendants left behind by
  earlier worker generations.
- Retargeting a deployment onto `home-pool`: on the current (3.7.2) server, changing a
  deployment's work pool via `prefect deploy` leaves the old `work_queue_id` — delete and
  recreate the deployment so it binds to `home-pool`'s queue.

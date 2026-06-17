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
- `prefect` is pinned to the server version (3.7.2) to avoid client/server skew.
- The unit sets `PATH` to include `~/.local/bin` because the process worker spawns
  `uv run …` for each flow run.
- Retargeting a deployment onto `home-pool`: on the current (3.7.2) server, changing a
  deployment's work pool via `prefect deploy` leaves the old `work_queue_id` — delete and
  recreate the deployment so it binds to `home-pool`'s queue.

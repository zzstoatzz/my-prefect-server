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
  local worker health endpoint for that. It never terminates flow-run processes;
  terminal state belongs to the flow engine and is not evidence that process
  teardown has finished.
- Retargeting a deployment onto `home-pool`: on the current (3.7.2) server, changing a
  deployment's work pool via `prefect deploy` leaves the old `work_queue_id` — delete and
  recreate the deployment so it binds to `home-pool`'s queue.

## toolchain on the box (2026-09-03)

Everything the worker's flows shell out to lives under `/home/stoat/.local`,
installed by hand. Nothing upgrades itself, so `just heavypad-status` is the
check and this table is what it should agree with.

| tool | how it is installed | required by | upgrade |
|---|---|---|---|
| `uv` 0.9.x | `~/.local/bin/uv` (standalone installer) | every flow run (`uv run --with …`) | `uv self update` |
| python 3.14.2, 3.13.11 | uv-managed (`uv python install`) | 3.14 default; 3.13.11 for the atlas/transform pins in `prefect.yaml` | `uv python install <ver>`; remove strays with `uv python uninstall` |
| node | tarball under `~/.local/node-v<ver>-linux-x64`, symlinked from `~/.local/bin` (`node`, `npm`, `npx`, `corepack`) | pi | download the new tarball, re-point the four symlinks, delete the old dir. pi 0.84 needs ≥ 22.19 |
| pi | `npm install -g --prefix /home/stoat/.local @earendil-works/pi-coding-agent@<ver>` | `pi-pr`, `autofix`, `pi-agent` | same command with the laptop's version (`pi --version` there) |
| pi's Codex login | `/home/stoat/.pi/agent/auth.json`, minted on the box by pi's device-code flow, never copied from a laptop | `pi-pr` (provider `openai-codex`, Luna) | `pi` → `/login` → OpenAI → device code, from any ssh session; pi ≥ 0.84 refreshes it |
| zig | `~/.local/opt/zig-<ver>` | building `prefect-worker-guard` (`install.sh`) | replace the dir and symlink |
| llama.cpp, zed, rclone, hydroxide, allegedly | `~/.local` / `~/.cargo` | not the worker | out of scope here |

`MPS_PIN` is set by CI, so a flow run installs exactly the deployed commit
from the GitHub mirror; push `github` as well as `origin` or the worker keeps
running the old code. uv's `archive-v0` cache (one env per pinned commit)
grows without bound; `uv cache prune` on the box is the maintenance step. A
cron (`hub-data-sync.sh`, every 3 min) and a user service (`hydroxide`) also
run as `stoat` and are not part of this deployment.

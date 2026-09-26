# laptop-worker

launchd agent for a Prefect **process** worker on the operator's Mac, polling
the prod server for the `laptop-pool` work pool. It runs only while the Mac is
awake and logged in, which is the point: work routed here happens when the
operator's machine is on, and waits (or is cancelled by the deployment's
collision strategy) when it is not. See
[fastmcp-attention](../../docs/fastmcp-attention.md).

## install

```sh
just laptop-worker install
```

Prereqs:
- `uv` at `~/.local/bin/uv`
- `~/.config/prefect-laptop-worker/env` (mode 0600), rendered by the secrets
  store (`env_files.laptop-worker`, `just sync` in the store). It holds the
  same `PREFECT_API_URL` / `PREFECT_API_AUTH_STRING` pair as heavypad's worker.
- the `laptop-pool` work pool exists on the prod server
  (`just prefect work-pool create laptop-pool --type process`).

## operate

```sh
just laptop-worker status     # launchd state + worker health endpoint
just laptop-worker restart    # e.g. after a credential sync
tail -f ~/Library/Logs/prefect-laptop-worker.log
```

## notes
- `prefect` is unpinned and refreshed on every start (`--refresh-package prefect --upgrade`), so this worker runs the newest release and is the canary for client changes against the zig server before heavypad (still pinned) follows.
- The health endpoint is on `127.0.0.1:8791` (8080 and 8765 are taken locally).
- heavypad's zig guard (which replaces a worker that is running but no longer
  polling) is systemd-specific and not used here. launchd `KeepAlive` restarts
  a worker that exits; a worker that wedges across sleep would not be caught.
  If that shows up, `just laptop-worker restart` and extend the guard.

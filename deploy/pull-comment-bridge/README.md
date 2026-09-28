# pull-comment-bridge

systemd **user** unit on heavypad running `mps.pull_comment_bridge`: it stays
subscribed to `stream.waow.tech` for the operator's and Phi's tangled comments
and emits `autofix.revise-requested` for each one that asks gardener for a new
round. The `autofix revise requested -> autofix-revise` automation
(`deploy/automations.yaml`) starts the run. Rung three of `docs/autofix.md`.

The hourly `watch-tangled-pulls` deployment is the safety net: it reconciles
against each reviewer's PDS and ends `Recovered` when it finds a comment the
bridge missed.

Verified 2026-09-28 with a synthetic event for a non-gardener pull (the
automation started `autofix-revise` about 5 s later with both parameters
rendered; it ended `Skipped`). A real operator comment on a gardener pull has
not been exercised yet; it costs a Pi run and publishes a round.

## state and health

- cursor: `~/.local/state/pull-comment-bridge/cursor`. First startup durably
  saves a timestamp five minutes in the past before connecting, even if no
  matching event arrives. Subsequent events checkpoint `time_us` only after
  delivery; failed writes do not advance the reconnect cursor. Corrupt cursor
  files fail startup instead of silently skipping to live.
- dedupe: the `autofix_handled_comments` Variable, shared with the reconcile flow
- health: `http://127.0.0.1:8791/health` — 503 once the subscription has been down
  for 3 minutes. `fleet-health` checks it every 15 minutes, so a dead bridge pages
  through `fleet unhealthy -> discord`.
- Prefect API credentials come from `~/.config/prod-worker/env`, like the workers.
  The stream is public; no other secret.

The cursor file and containing directory are fsynced after atomic replacement.
The unit uses an explicit home-relative state path because `%S` resolved to
`~/.config` on heavypad's user manager. Preserve an existing cursor from that
legacy location when upgrading; do not replace a valid cursor with the current
time. A quiet subscription does not imply a fault or justify advancing it by
wall clock. Stream replay is bounded to 36 hours; the hourly PDS reconciliation
remains necessary for longer outages and first-start history.

The checkpoint fix was deployed on 2026-09-28 at 08:41 Chicago time. The saved
cursor survived an actual service restart and matched the health response
after reconnecting. A separate production Stream capture/reconnect replayed
the identical event. PDS reconciliation found two relevant comments, both
already handled; verification did not publish a comment or start a revision.

## install or upgrade

The unit runs a staged mps wheel, the same way wheel-pinned deployments do. To
upgrade, stage a new wheel, put its sha256 in the unit's `ExecStart`, commit, and
install the committed unit.

```sh
uv build --package mps --wheel -o dist/
sha=$(shasum -a 256 dist/mps-0.1.0-py3-none-any.whl | cut -d' ' -f1)
tailscale ssh stoat@heavypad "mkdir -p ~/phi-spike-worker/releases/$sha"
tailscale ssh stoat@heavypad "cat > ~/phi-spike-worker/releases/$sha/mps-0.1.0-py3-none-any.whl" < dist/mps-0.1.0-py3-none-any.whl
tailscale ssh stoat@heavypad 'cat > ~/.config/systemd/user/pull-comment-bridge.service' \
  < deploy/pull-comment-bridge/pull-comment-bridge.service
tailscale ssh stoat@heavypad 'systemctl --user daemon-reload && systemctl --user enable --now pull-comment-bridge && systemctl --user restart pull-comment-bridge'
```

## operate

```sh
systemctl --user status pull-comment-bridge
journalctl --user -u pull-comment-bridge -f
curl -s http://127.0.0.1:8791/health
```

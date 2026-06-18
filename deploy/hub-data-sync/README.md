# hub data sync (heavypad → EU edge)

The hub (`hub.waow.tech`) runs **in-cluster on the EU Hetzner node** (`deploy/hub-deployment.yaml`),
so the page renders EU-local and fast. The flows that produce its data run on
**heavypad** (the home box). This sync keeps the edge's copy fresh.

## why not just serve the hub from heavypad?

We tried (point the ingress at a hub container on heavypad over the tailnet —
see git history for the reverted `hub-remote.yaml`). It was ~12s/load: every
request went US-user → EU-edge → US-home and streamed a ~400KB page back over a
DERP-relayed residential link. Render on heavypad itself was ~12ms — the cost
was entirely the cross-Atlantic transfer of the page. The data is only ~4MB, so
the right split is **serve at the edge, sync the data**: ~4MB crosses the
tailnet every few minutes, off the request path, instead of ~400KB on every
page load.

## how it works

- `sync.sh` runs on **heavypad** via cron, every 3 min:
  `*/3 * * * * /home/stoat/hub-data-sync.sh >> /home/stoat/hub-data-sync.log 2>&1`
- It `rsync`s `hub.duckdb` + `llm-spend.jsonl` from
  `/home/stoat/prefect-analytics` to the EU node's `/var/lib/prefect-analytics`
  (the hostPath the hub Deployment mounts read-only).
- `llm-spend.jsonl` is append-only so rsync transfers only new bytes; `hub.duckdb`
  changes only when `transform` runs. Most syncs move almost nothing.
- Dashboard freshness lag is therefore ≤ ~3 min, which is fine for a cost panel.

## auth

heavypad pushes over the tailnet using a dedicated key `~/.ssh/hub_sync_ed25519`.
Its pubkey is in the EU node's `/root/.ssh/authorized_keys`, restricted to
`from="<heavypad-tailnet-ip>",no-agent-forwarding,no-port-forwarding,no-pty`.

## re-establish from scratch

```sh
# on heavypad
ssh-keygen -t ed25519 -N "" -f ~/.ssh/hub_sync_ed25519 -C "hub-data-sync@heavypad"
# on the EU node (as root), add the restricted pubkey
echo 'from="100.96.216.23",no-agent-forwarding,no-port-forwarding,no-pty ssh-ed25519 AAAA...' >> ~/.ssh/authorized_keys
# back on heavypad: drop sync.sh at ~/hub-data-sync.sh, chmod +x, add the cron line above
```

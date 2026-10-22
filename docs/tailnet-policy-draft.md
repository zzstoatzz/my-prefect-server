# tailnet policy draft

Status: draft for nate to review and apply in the Tailscale admin console
(Access controls). Written 2026-10-22. Nothing here is applied yet.

Today the tailnet is allow-all: every tailnet address reaches every port on
every device (seen in the Mac's packet filter), and no device is tagged. That
is fine for nate's own devices, but no outside machine (a gardener exe.dev VM)
may join until it changes.

These are edits to merge into the existing policy, not a replacement. Keep any
existing `ssh`, `nodeAttrs` (heavypad's Funnel needs one) and `hosts` entries.

## part A: apply now

1. Remove the allow-all rule. It usually looks like one of:

   ```hujson
   {"action": "accept", "src": ["*"], "dst": ["*:*"]}   // in "acls"
   {"src": ["*"], "dst": ["*"], "ip": ["*"]}             // in "grants"
   ```

2. Add:

   ```hujson
   "tagOwners": {
     "tag:gardener-vm":    ["autogroup:admin"],
     "tag:prefect-server": ["autogroup:admin"],
   },

   "hosts": {
     // hetzner-edge; replaced by tag:prefect-server in part B
     "prefect-server": "100.78.52.120",
   },

   "grants": [
     // nate's own devices: unchanged from today
     {"src": ["autogroup:member"], "dst": ["*"], "ip": ["*"]},

     // a gardener run reaches the Prefect API and nothing else
     {"src": ["tag:gardener-vm"], "dst": ["prefect-server"], "ip": ["tcp:443"]},
   ],
   ```

What changes for existing devices: nothing for devices you own (Mac, phone,
heavypad, hetzner-edge, the ai/Aperture node, the second rex host, the offline
aperture-567107 iOS device), since they are all members' devices. A device shared *into* this tailnet from another
tailnet is not a member and would lose access. None is known, but check the
Machines page for "shared" devices before applying.

Check after applying: from the Mac, `ssh heavypad` and `ssh hetzner-edge`
still work, `kubectl get nodes` (my-prefect-server kubeconfig) answers, and
heavypad's workers keep heartbeating.

## part B: after the server inventory

Tagging removes hetzner-edge from `autogroup:member`, so it loses all outbound
reach except what is granted. The deploy thread inventoried what the node starts
into the tailnet (2026-10-22, from live state and every k8s object naming a
tailnet host); pod traffic leaves as the node, so both are granted by the
node's tag:

| from | to | why | breaks if missing |
|---|---|---|---|
| prometheus pod | heavypad tcp:9100 | node_exporter scrape (`job_name: heavypad`) | heavypad dashboard and its reachability alert |
| traefik | heavypad tcp:6555 | `spindle.zzstoatzz.io` → selectorless service `prefect/spindle-heavypad` | tangled's appview reaching the spindle (CI) |

Confidence gap: the node has no conntrack, so a pod dialing an unlisted 100.x
address would not have shown up. Watch the spindle and the heavypad alert right
after tagging.

1. Apply the policy edit first, so the grants exist before the tag does:

   ```hujson
   "hosts": {
     "heavypad": "100.96.216.23",
     // "prefect-server" alias removed
   },

   "grants": [
     {"src": ["autogroup:member"], "dst": ["*"], "ip": ["*"]},
     {"src": ["tag:gardener-vm"], "dst": ["tag:prefect-server"], "ip": ["tcp:443"]},
     {"src": ["tag:prefect-server"], "dst": ["heavypad"], "ip": ["tcp:9100", "tcp:6555"]},
   ],
   ```

2. Then tag hetzner-edge `tag:prefect-server` (Machines page → Edit ACL tags).
3. Check: spindle.zzstoatzz.io answers; Prometheus target `heavypad` is up;
   `ssh hetzner-edge` and kubectl still work from the Mac (member → tagged
   node is covered by the first grant).

Side effect: the server can no longer reach the Mac or anything else on the
tailnet besides those two heavypad ports.

## public paths after the flip

Once `prefect-server.waow.tech` resolves to the tailnet address, off-tailnet
clients can't reach any path on it, so a path exception doesn't work. What
must stay public (the phone's Shortcuts presence call) moves to its own public
hostname: `hub.waow.tech/api/presence` behind Cloudflare with a path bypass.

## OAuth client for gardener keys

After part A (the tag must exist first): Settings → OAuth clients → Generate.

- Scope: **Auth Keys**, write.
- Tags: `tag:gardener-vm` only.
- Description: `gardener-exe worker: per-run ephemeral keys`.

Copy the client secret, then store it without displaying it. `sops set` takes
a JSON value, so the raw secret is wrapped as a JSON string first:

```bash
pbpaste | jq -Rs 'rtrimstr("\n")' | sops set --value-stdin ~/tangled.org/zzstoatzz.io/secrets/prod.yaml '["tailscale"]["gardener_oauth_client_secret"]'
```

The client ID isn't secret; it can go in the same section or in the worker's
config. The exe worker on heavypad mints a single-use, ephemeral,
pre-authorized `tag:gardener-vm` key for each run from this client.

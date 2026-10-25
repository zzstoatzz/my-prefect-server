# Isolated HeavyPad Prefect pilot

The pilot is a separate k3s cluster inside Docker (k3d), with its own PostgreSQL,
durable Redis, credentials, worker and deployment file. It uses the same immutable
server image as the current Hetzner instance. The original hostname, worker and
critical deployments remain on Hetzner. Never restore the production database
into a running pilot scheduler: copied deployments/automations could execute twice.

Kubernetes API binds to host loopback 16443; the application binds to loopback
14200. Tailscale Serve adds a distinct private HTTPS endpoint on port 10443.
Existing Serve/Funnel mappings are preserved. This requires Tailscale on clients;
it is not a public Funnel endpoint. Canonical-domain cutover is a later decision.

Source must be on main before applying. Run `cluster.sh` on HeavyPad after
installing checksum-verified k3d 5.9.0. Kubernetes is pinned to the current cloud
node's k3s release. Secrets come from the canonical encrypted store via stdin;
never commit rendered credentials or dump Kubernetes Secret objects. Persistent
volumes are under `~/.local/share/prefect-home/storage`. Do not delete the cluster
or its storage as an ordinary rollout operation.

Only low-risk flows qualify for this pilot. Verify a real manual run, persistent
state, backup/restore and authenticated remote access before transferring schedule
ownership. Pause the old schedule and cancel its queued scheduled runs before
activating the new one. Do not copy production alerting, Gardener, publishing,
email or Typeahead jobs as part of this goal. Migration records must identify the
old/new deployment IDs, source pin, schedule and rollback.

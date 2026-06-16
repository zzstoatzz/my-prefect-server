# EU migration playbook

This repo currently runs a single-node k3s Prefect control plane on Hetzner in
Ashburn. The cost review shows the main problem is location pricing: the same
`cpx31` class box is about 3.6x cheaper in Hetzner EU. A move to `fsn1`,
`nbg1`, or `hel1` should save about $53/month without changing the machine
shape.

The relay/zlay migration proved the savings are real, but also made the main
operational lesson painfully clear: preserve topology. Do not consolidate
independent services onto one node just because that is temporarily convenient.
For this repo the intended topology is simpler: one Prefect node moves from US
to EU.

Hetzner quota/capacity currently does **not** allow a parallel Prefect node.
This is a delete-first migration: fully export the old node's reconstructable
and stateful pieces, delete the Ashburn server, create the EU replacement, then
restore/redeploy. Do not try to allocate the EU node before tearing down the
existing one.

## Target state

- one Hetzner EU server named `prefect-server`
- an available EU server class with enough headroom
- k3s installed from cloud-init
- existing Kubernetes workloads redeployed from this repo
- persistent state restored from local backups
- public hostnames moved only after the EU node is proven healthy

Prefer `fsn1` unless there is a specific reason to pick another EU region. The
current `cpx31` has EU pricing around $20/mo but may not be orderable in EU
anymore. Check availability immediately before deletion and choose the closest
available replacement (for example a current-generation 4 vCPU / 8 GB class) if
`cpx31` is unavailable.

Current check on 2026-06-16: `cpx31` is not orderable in EU from this project,
but `cpx32` is available in `fsn1`, `nbg1`, and `hel1` with the same 4 vCPU /
8 GB / 160 GB shape. Use `cpx32` unless a better available type is deliberately
chosen.

Terraform state is not currently usable in this checkout, so do not rely on
`just infra` / `terraform apply` for the actual recreation unless state is
restored first. The practical recreation path is Hetzner CLI/API using the
existing `prefect-server-key` SSH key and `prefect-server-fw` firewall.

## Preflight

Before deleting anything:

1. Verify current inventory with the Hetzner API, not memory.
2. Record the current server ID, IP, type, location, volumes, and DNS records.
3. Confirm the app's persistent state:
   - Postgres data
   - Prefect work pools/deployments/blocks
   - `/var/lib/prefect-analytics/llm-spend.jsonl` (**must preserve for
     hub.waow.tech LLM spend**)
   - `/var/lib/prefect-analytics/analytics.duckdb`
   - `/var/lib/prefect-analytics/hub.duckdb`
   - any other local files used by flows, logs, or hub publishing
   - Grafana/Prometheus state if it matters
4. Check whether the current `kubeconfig.yaml` points at the US node.
5. Check which public hostnames are served by this cluster.
6. Confirm which EU server types are orderable after deletion.
7. Confirm DNS update access. The relevant hostnames are currently Cloudflare
   DNS records for `prefect-server.waow.tech`, `prefect-metrics.waow.tech`, and
   `hub.waow.tech`, all pointing at the Ashburn IP. The Prefect
   `cloudflare-api-token` block verifies as a token but does not list the
   `waow.tech` zone, so it is not sufficient for automated cutover unless that
   token's permissions are changed or a different token is provided.

Do not delete the US node until the backup bundle has been created and verified
locally. After deletion, rollback is DNS/recreate-from-backup rather than
instant failback.

## Delete-first sequence

1. Build a local backup bundle.

   Store it outside the repo, chmod 700, and do not commit it. Include:

   - old `kubeconfig.yaml`
   - repo `.env`
   - Hetzner inventory JSON for the old node
   - rendered Kubernetes resource YAML for namespaces that matter
   - Kubernetes Secrets YAML, because registry creds/auth/certs may be needed
   - a Postgres dump from `prefect-postgres`
   - an rsync/scp copy of `/var/lib/prefect-analytics/`
   - explicit checksums/sizes for `llm-spend.jsonl`
   - DNS records observed before the move

   Initial backup created 2026-06-16:

   - `/Users/nate/.codex/migration-backups/prefect-eu-20260616T193601Z`
   - `llm-spend.jsonl`: 1,852,898 bytes, SHA-256 recorded in the backup
   - `analytics.duckdb`: about 1.4 GB
   - `hub.duckdb`: about 18 MB
   - Postgres dump: about 28 MB gzip
   - Kubernetes namespace YAML, Secrets YAML, Helm releases, Hetzner inventory,
     DNS observations, and old kubeconfig are included
   - local-path PVC data sync was also started because it is cheap insurance for
     Prefect result/cache state

2. Stop writers and take a final delta backup.

   Pause schedules or stop the worker/background services so Postgres and the
   analytics directory are not changing. Then repeat the Postgres dump and the
   `/var/lib/prefect-analytics/` sync. Verify `llm-spend.jsonl` exists in the
   backup and has the expected byte count.

3. Delete the Ashburn node.

   Use the Hetzner API/CLI with the repo's token. Keep the recorded old server
   ID/IP in the migration notes. Expect public service downtime from this point
   until DNS is moved to the EU node.

4. Create the EU node.

   Use the same `server_name` (`prefect-server`) and the chosen EU location.
   Prefer repo Terraform if state is usable; otherwise create with the Hetzner
   CLI/API using the same cloud-init behavior: Ubuntu 24.04, firewall for
   22/80/443/6443, SSH keys, and k3s with the public IP as TLS SAN.

5. Fetch and save the EU kubeconfig.

   Replace the working `kubeconfig.yaml` only after saving the old one in the
   backup. When switching shells, be explicit about `KUBECONFIG` so commands do
   not accidentally hit the wrong cluster.

6. Deploy platform dependencies.

   Install cert-manager, issuers, Postgres, monitoring, and the Prefect server
   using the repo's existing `just` targets and manifests. Avoid inventing a new
   deployment path during migration.

   Fresh-node bootstrap notes from the 2026-06-16 move:

   - `just publish-server-remote` builds on the Hetzner node with buildah/podman.
     Runtime Dockerfiles must use fully-qualified Docker Hub image names such as
     `docker.io/prefecthq/prefect:3-python3.14-kubernetes`; otherwise podman may
     reject short names on a clean node.
   - Grafana's DuckDB dashboard requires the MotherDuck DuckDB datasource plugin
     at `/var/lib/grafana-plugins/motherduck-duckdb-datasource` before the
     Grafana pod can mount it. Install the plugin directory on the node before or
     during monitoring deployment.
   - Restoring a Python-server/Postgres dump into the Zig schema may leave tables
     missing constraints that `CREATE TABLE IF NOT EXISTS` cannot add later. Check
     at least `worker(work_pool_id, name)` and `work_queue(work_pool_id, name)`
     unique indexes before starting the worker.

7. Restore state.

   Restore the Postgres dump before starting normal Prefect work. Restore
   `/var/lib/prefect-analytics/` onto the node before deploying the hub/worker.
   Confirm `llm-spend.jsonl` is present on the EU node before declaring the hub
   healthy; this file backs the live LLM spend panel.

   Before enabling the worker, verify the restored `kubernetes-pool` is actually
   a Kubernetes work pool with a non-null `default_queue_id`, and that all
   deployments using `work_pool_name='kubernetes-pool'` point at the current
   default queue. If repair is needed, prefer recreating the pool through the API
   with Prefect's Kubernetes base job template, then update deployment and
   non-terminal flow-run queue IDs to the new default queue.

   Do not let the worker drain every overdue scheduled run after downtime. Either
   cancel stale overdue runs first or set an explicit queue/work-pool concurrency
   limit before scaling the worker up. Kubernetes will keep unschedulable pods
   pending, but without API throttling the worker can still create a large burst
   of Jobs.

8. Verify the EU node before DNS.

   Test from inside the cluster and externally with forced IP/SNI:

   ```sh
   curl --resolve "$DOMAIN:443:$EU_IP" "https://$DOMAIN/api/health"
   curl --resolve "$GRAFANA_DOMAIN:443:$EU_IP" "https://$GRAFANA_DOMAIN/"
   ```

   Also verify pod readiness, ingress addresses, certificates, and Prefect API
   auth. Do not count a pod becoming Ready as the whole migration being done.

9. Cut DNS.

   Move only the Prefect hostnames to the EU IP. Keep records DNS-only if they
   are currently DNS-only. After the update, verify multiple resolvers:

   ```sh
   dig +short "$DOMAIN" @1.1.1.1
   dig +short "$DOMAIN" @8.8.8.8
   dig +short "$GRAFANA_DOMAIN" @1.1.1.1
   ```

10. Watch stale clients.

   The relay/zlay move showed that browser and recursive DNS caches can keep
   hitting the old IP after public DNS is correct. If necessary, add a temporary
   compatibility ingress on the old node for the moved hostnames, with the
   correct TLS secret, and proxy traffic to the EU node. Treat that as a
   time-boxed drain route and write down when to remove it.

11. Confirm the Ashburn line item is gone.

   The old node was already deleted before recreation. After the EU node is
   healthy, confirm the cost connector no longer sees the Ashburn server and
   the new EU server is the only Prefect node.

## Verification checklist

- Hetzner API shows `prefect-server` in EU, not `ash`.
- `terraform plan` matches the intended final state.
- `kubectl get nodes -o wide` shows the EU node IP.
- All Prefect pods are Ready.
- Postgres data is present.
- `/var/lib/prefect-analytics/llm-spend.jsonl` is present and has the backed-up
  byte count or a known post-restore delta.
- Hub LLM spend panel shows historical spend, not a reset-to-zero dashboard.
- Prefect UI loads.
- Prefect API auth works.
- Existing deployments/work pools are visible.
- A known flow can run successfully.
- Grafana/metrics hostname has a valid certificate.
- HTTP and HTTPS public routes return expected status codes.
- Forced old-IP checks either fail in a known way or hit the intentional
  temporary compatibility ingress.
- Cost snapshot drops from about $73/month to about $20/month for this node.

## Rollback

Rollback is less comfortable in delete-first mode, so keep it explicit:

- Keep the old kubeconfig and DNS values in the migration notes.
- Keep the full local backup bundle until the EU node has run normally for a
  few days.
- If EU creation/deploy fails, recreate a US node from the same backups and
  point DNS back to the recreated service.
- If state was modified on the EU node after cutover, dump it before replacing
  the node again.

## Follow-up after success

- Update `COST-REVIEW.md` to `RESOLVED`.
- Record the final server ID, IP, type, and location.
- Remove any temporary compatibility ingress.
- Run the cost collection flow so the public dashboard reflects the savings.
- Consider a second pass to downsize only after observing real EU-node resource
  usage under normal scheduled flows.

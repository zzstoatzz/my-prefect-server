# home Prefect operations

HeavyPad owns the production Zig Prefect control plane, PostgreSQL, Redis and
process workers. Public routing reaches `https://prefect-server.waow.tech`.
The original Hetzner node is deleted; its [retirement receipt](../../docs/audits/2026-10-27-prefect-retirement.md)
and [migration records](../../docs/migrations/) are historical evidence.

## access and server rollout

```sh
just prefect deployment ls
just kubectl -n prefect-home get pods
just heavypad-status
just prefect-secrets
```

[targets.toml](../targets.toml) selects the endpoint, SOPS credential and SSH
host. The kubectl wrapper uses the host Docker socket and the k3d server
container. It never uses the laptop's current Kubernetes context. The private
Tailscale Serve API is on port 10443; containers use the separately configured
host proxy because bridge-network containers cannot reach that Serve listener.

Land source on main before applying. [cluster.sh](cluster.sh) creates the home
k3d cluster with explicit loopback port bindings and the pinned k3s image. Run
it only on HeavyPad with the installed k3d tool. Persistent volumes live under
`~/.local/share/prefect-home/storage`; deleting the cluster or that directory is
not a rollout procedure. The Kubernetes API binds to loopback 16443 and the
application to loopback 14200. Preserve existing Tailscale mappings.

[stack.yaml](stack.yaml) owns image pins, resources, server settings and replica
counts. For an API-only change, inspect the diff and apply only the API Deployment
through `just kubectl -n prefect-home apply -f -`. Do not restart background
services unnecessarily. Verify rollout, a real deployment run and output.

API and services each have two replicas on one k3d node. Redis consumer groups
coordinate background work; delivery is at least once. This covers a pod
restart, not loss of the host. Preserve the qualified handler counts in the
manifest; see the [HTTP pool qualification](../../docs/audits/2026-11-06-http-thread-pools.md)
and [services replica qualification](../../docs/audits/2026-11-14-two-services-replicas.md).

## workers and flow interpreters

The default `prefect-heavypad-worker.service` and the background, build,
interactive and gardener instances of `prefect-heavypad-worker@.service` run
process jobs. Their [base units](worker@.service) and `worker-*.conf` overrides
own slot and resource limits; the corresponding pools must agree. The default
worker has its own [unit](worker.service). Read the deployment's pool before
choosing a worker to inspect.

```sh
ssh stoat@heavypad 'systemctl --user list-units "prefect-heavypad-worker*" --all --no-pager'
ssh stoat@heavypad 'journalctl --user -u prefect-heavypad-worker@background -n 100 --no-pager'
just prefect work-pool ls
```

These units use `KillMode=control-group`: drain affected runs before stopping or
replacing a worker. The old system worker's process-survival guard is not the
current restart contract. The separate `gardener-exe` pool's `pi-exe-worker`
was disabled at the November 15 audit; do not enable it or resume the suspended
autofix chain as routine repair. See [autofix](../../docs/autofix.md).

Workers read `~/.config/prefect-home/worker.env`. `UV_PYTHON` in the unit selects
the interpreter inherited by `uv run`; an explicit command `--python` overrides
it. Keep `~/.cargo/bin` ahead of the distribution toolchain for dependencies
that need source builds. Do not remove command-prefix Prefect settings as
redundant: worker settings merging can otherwise override the deployment's
storage path.

Persistent analytics and local storage are under `/home/stoat/prefect-analytics`.
Dagster's two user services use [dagster-prefect.conf](dagster-prefect.conf) for
the same server's analytics writer lease. Drain writers before changing that
client. Readers snapshot shared DuckDB files; all read-write opens hold the
`analytics-duckdb-writer` lease.

## persistence, backups and recovery

PostgreSQL and Redis use persistent volumes. Redis uses synchronous AOF and
`noeviction`; at its memory cap, writes and lease acquisition fail. Diagnose
capacity or stalled consumers rather than flushing streams or bypassing the
writer lease. Verify lease acquire/release and real execution after repair.
See the [November 6 incident](../../docs/incidents/2026-11-06-home-internet-autofix-cascade.md).

The backup, offsite and restore-verification units invoke
[prefect-dr](https://github.com/prefectlabs/prefect-dr), installed on the host
from its wheel. [prefect-dr.toml](prefect-dr.toml) is installed at
`~/.config/prefect-dr/heavypad.toml`; the units read bucket credentials from
`~/.config/prefect-dr/env`. The older `offsite.sh` and `offsite-verify.sh`
installers are gone. Read the installed unit and package version before changing
this interface.

- `prefect-home-backup.timer` runs daily at 05:45 UTC with fixed jitter. Dumps
  are age-encrypted and kept under `~/.local/state/prefect-home/backups` for
  seven days, separate from cluster storage.
- A successful backup starts `prefect-home-offsite.service`; its timer retries
  daily at 17:45 UTC. R2 receives ciphertext under `postgres/`, with immutable
  object writes and local/remote verification. Local retention does not delete
  offsite copies. Preserve the bucket's lock and lifecycle policy.
- `prefect-home-offsite-verify.timer` restores the newest offsite dump into an
  isolated throwaway PostgreSQL. It checks expected deployment names and recent
  completed runs, then writes `restore-verified.json`. It starts no Prefect
  scheduler or automation service. Expected names and the restore image are in
  `prefect-dr.toml`.
- [host-backup.sh](host-backup.sh), daily at 06:15 UTC, encrypts host state that
  the database dump does not contain: units, referenced environments, ingress,
  keys, selected local state, the UI archive and Redis AOF. It uploads under
  `host/` and writes `host-backup.json`. This is a selected-path backup:
  root-owned spindle state, Prefect results/storage and edge/Dagster persistent
  state are excluded. See the [recovery coverage](../../docs/heavypad-outage-runbook.md)
  before relying on it to rebuild a service.

```sh
just prefect-backup
just prefect-offsite
just prefect-offsite-verify
ssh stoat@heavypad 'systemctl --user list-timers "prefect-home-*" --all'
```

`backup-freshness` checks local dump, offsite and host-snapshot freshness plus
weekly restore verification. Its notification depends on the home server, so
it cannot detect total host loss. Use the [outage runbook](../../docs/heavypad-outage-runbook.md)
for recovery and off-host checks. Never attach a restored database to a second
active scheduler or enable copied automations by default.

`just prefect-secrets` verifies the consumers it renders, including
`~/.config/prefect-home/r2.env`; it does not verify the separately installed
prefect-dr environment. When rotating bucket credentials, reconcile both
consumers before exercising backup/offsite/verify. API auth rotation requires
re-rendering and restarting its consumers. Changing a Kubernetes Secret does
not change an initialized PostgreSQL role's password.

The current UI image is pinned in `stack.yaml`. The old `3c108a1` UI tag is
unavailable in the registry; its recovery archive remains at
`~/.local/share/prefect-home/images/prefect-ui-3c108a1.tar`. A rollback to that
specific version requires importing the archive. Its manifest digest is
`sha256:33a07cc4c10caf78af728e484447620c5c80b4d26e5a40b366cc7fc76cad95c3`.

## host startup

Docker starts at boot, k3d containers use `unless-stopped`, and the user manager
has lingering enabled. `prefect-home-k3s-guard.service` waits for `/readyz` and
can restart the server container at most twice when k3s fails inside it. Inspect
`systemctl --user --failed` if recovery does not complete.

Load `br_netfilter` on the host and persist it in
`/etc/modules-load.d/br_netfilter.conf`. The k3d node cannot load host modules;
without it, pods can fail to reach service IPs even while k3s reports ready.
This remains one host and one disk, not host-level high availability.

## registering main revisions

Spindle microVMs intentionally block private networks, including Tailscale IPs
and MagicDNS answers. CI validates source without Prefect credentials. Deployment
registration runs on the trusted HeavyPad host, using its existing
`~/.config/prefect-home/client.env` derived from canonical SOPS.

`prefect-home-registration.timer` checks every five minutes. Its oneshot service
fetches GitHub main into a dedicated clean clone, requires the matching Tangled
`deploy.yml` push pipeline to be green, verifies the existing HeavyPad process
pool, then validates and registers `prefect.heavypad.yaml` with the exact
`MPS_PIN=@<SHA>`. It records success in
`~/.local/share/prefect-home/registration/registered-sha`. A lock prevents
concurrent registration; the same successful SHA is a no-op. Failed or pending
CI leaves the previous deployed revision in place. Keep GitHub and Tangled main
in sync with `just push`.

Registration does not use the host's Python settings. The unit runs the
script on a uv-managed interpreter, and the `uv run` steps inside the clone use
the repository's `.python-version`, the same minor version CI checks. The
host's global uv pin and the apt `python3` can change without affecting it.

Reconcile or inspect from an operator checkout:

```sh
ssh stoat@heavypad 'systemctl --user start prefect-home-registration.service'
ssh stoat@heavypad 'journalctl --user -u prefect-home-registration.service -n 12 --no-pager'
ssh stoat@heavypad 'systemctl --user list-timers prefect-home-registration.timer'
```

For an intentional manual rollback, stop the registration timer first so it
does not restore main's declarations. Register only the intended rollback
revision against the verified target, keep one scheduler owner, then reconcile
main before re-enabling the timer. Runtime state and secrets are not restored
by checking out source.

Source and units are `scripts/register_heavypad_main.py`,
`deploy/heavypad-prefect/prefect-home-registration.service` and its `.timer`.
The installed script is `~/.local/share/prefect-home/register-main.py`; install
updated source there after landing it on main, then reload the user manager when
units change. The oneshot reads freshly rendered credentials on every start.

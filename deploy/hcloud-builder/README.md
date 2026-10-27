# Ephemeral cloud builder

The HeavyPad worker provisions one cpx32 (160 GB scratch disk) per run, polls
the private home Prefect API through a single-use Tailscale enrollment, and
destroys the VM after the durable supervisor outcome is reconciled. Source
exports, build scratch and R2 publications stay in the cloud. Home has a
1 TB/month combined traffic budget; never mount home storage for this job.

The supervisor survives worker disconnects. Its result and bootstrap timing
receipts are copied into worker observations before deletion. Each attempt has
a separate SSH identity. Server names, ownership labels, run IDs and pool IDs
must agree before mutation. IP cleanup retries eventual assignment state;
saved resource receipts allow cleanup after a server has already disappeared.

`uv run python scripts/qualify_hcloud_builder.py --output /tmp/qualification.json`
creates a real VM and checks success, failure, timeout, cancellation, restart
without replay, 104 GiB available scratch, the Zig compiler, and absence of the
VM, SSH key and primary IP after cleanup. It incurs one short VM charge.

Render the existing project credential with
`uv run python scripts/heavypad_hcloud_client.py --apply`; running it again
verifies the same bytes. The source is canonical SOPS
`prefect.blocks.hetzner-tokens.hetzner-prefect-server-api-token`. The worker
reuses the existing canonical `client.env` and `gardener-tailnet.json`.
No credential issuer rotation is performed. Consumer rotation is render,
drain, restart, real probe. Issuer creation/revocation still requires the
Hetzner console; it is not a one-command rotation path.

After source is committed and pushed, install with
`bash deploy/hcloud-builder/install.sh FULL_COMMIT`. The immutable release is
installed separately from all other workers. Installation refuses active
owned builder VMs. The new pool must remain manual-only until a real Prefect
probe and independent expiry cleanup are verified. Then move the index
deployment's pool and retain its one existing schedule; never register a
second live publisher clock.

Infrastructure qualification, private API access, real publication and cleanup
passed before the original node was retired. Typeahead's separate serving
latency admission remains independent of infrastructure qualification. See the
[retirement audit](../../docs/audits/2026-10-27-prefect-retirement.md).

## Billing evidence

The manager preserves non-secret lifecycle and rate receipts in
`~/hcloud-builder/state/billing/`, outside per-attempt cleanup directories.
`hcloud-builder-billing-offsite.timer` copies them to the existing R2 backup
bucket each minute with immutable/checksum checks. The independent expiry reaper
also writes R2 receipts. The cost collector merges by server ID and publishes
a labelled month-to-date amount with observed spend and coverage in its note.
Creation/deletion evidence survives VM, key and primary-IP deletion. A billing
capture failure does not keep a VM alive; its resource receipt stays for retry.

Inspect `systemctl --user status hcloud-builder-billing-offsite.timer` and
`journalctl --user -u hcloud-builder-billing-offsite.service` for archive health.
The timer reuses the existing canonical `~/.config/prefect-home/r2.env` consumer;
no credentials are sent to builder VMs or embedded in billing evidence. Rotation
uses the existing R2 render/check command and the next timer invocation.

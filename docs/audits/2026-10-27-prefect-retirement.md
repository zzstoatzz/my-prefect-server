# Prefect node retirement

Hetzner server `142141293`, its 150 GB volume and both primary IPs were deleted
on October 27 at 15:43 UTC. Provider API reads verified their absence. The
[receipt](2026-10-27-prefect-retirement.json) records resource IDs and verification.

Prefect, push, Hub and Grafana now run at home. Their existing public hostnames
use the home Cloudflare tunnel; Spindle was already at home and no longer uses
the retired proxy. Authenticated API reads, push subscription, Grafana health,
Spindle and a real deployment canary succeeded with the old node offline.
Dagster and deliberately paused/retired workflows retain their agreed state.

Control-plane history, push SQLite, results, monitoring history and retained PDS
coverage data have encrypted R2 archives. The old PostgreSQL dump restored into
an isolated database; push integrity and result extraction passed. Redis was
archived but not separately restored. A fresh home backup restored from R2 at
15:38 UTC with 48 deployments, 880 runs, 44 blocks and 26 automations.

The watchdog was alive: its heartbeats had moved to home while its absence rule
remained on Hetzner. Bandwidth/heat rules now run at home, old copies are disabled,
and the missing Logfire write block was copied unchanged and hash-verified.
The watchdog restarted and emitted real heartbeats every minute.
Autofix now uses the canonical `/runs/flow-run/<id>` URL rather than internal
API addresses. Revision `f0f9af1` passed full CI and was registered at home;
UI environment updates preserved the other deployment pins and schedules.

The ephemeral builder built and published `b1793109259-2b36-px05`, then its
serving candidate failed the latency admission guard. The VM, key and IP were
cleaned up. This full run remains **Failed**; successful publication does not
qualify the serving candidate. The serving guard and existing route were preserved.
Earlier successful full-run evidence remains in the migration records.

## Cost dashboard audit

The 15:43 UTC collection published 81 lines totaling $493.94, compared with the
08:01 snapshot's $548.59. The deleted server, volume and IPv4 lines are absent;
their former list-price total was $54.10/month. The provider reports USD and 0%
VAT for this project. These are mixed run-rate/usage estimates, not an invoice.

The total is incomplete: 15 stopped Fly root filesystems are unmeasured, Coral's
usage window was shorter than 60 minutes, and inventory alone misses ephemeral
VMs deleted between collections. Builder cleanup removes its local resource
receipt, so historical attempt accounting needs a durable cost ledger. Two R2
buckets (`strata-archive`, `strata-archive-private`) remain unattributed.
R2 operation/egress and home power/network costs are not comprehensive. Coverage
gaps appear in the Prefect state but are not carried in the public snapshot;
the dashboard can therefore show a total without the associated warning.

Builder resource names currently match the generic `prefect` attribution rule,
although this builder executes Typeahead. A durable ledger should preserve the
workload owner as well as creation/deletion time and hourly price.

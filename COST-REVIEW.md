# ✅ cost review — RESOLVED 2026-06-16

_tracked via the [evergreen cost snapshot](https://tangled.org/zzstoatzz.io/evergreen) (`io.zzstoatzz.cost.snapshot` on pds.zzstoatzz.io). The cost connector hub that produces these snapshots lives in this repo (`flows/costs.py`, `packages/mps/src/mps/costs/`)._

The single-node k3s box was moved out of the US datacenter premium.

| | type | location | monthly |
|---|---|---|---|
| before (2026-06-15) | cpx31 (4 vCPU / 8 GB) | ash (US, Ashburn) | $73.49 |
| after (2026-06-16) | cpx32 | fsn1 (Germany) | **$41.99** |

**Savings: ~$31.50/mo (~$378/yr).** (Net of a slight cpx31→cpx32 bump; the move alone off the US premium is the win.)

Verified against the Hetzner API: this node now runs in `fsn1` (Germany), IP `167.233.79.205`.

_No action needed. Left for the record._

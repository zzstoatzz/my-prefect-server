# Cost accounting and bandwidth verification

The new accounting and monitoring are deployed. Detailed evidence is in the
[verification receipt](2026-10-27-cost-and-bandwidth-verification.json).

- A real home-server cost run, `0e81de31-7e94-4028-9d3f-80f582fef77c`,
  published 85 items at 20:56:20 UTC. The public record, artifact, totals and
  rollups match exactly. Its $514.91 total mixes labelled monthly rates and
  month-to-date usage; it is not a recurring forecast or invoice.
- OpenRouter reports $1.12 month-to-date for the configured shared key.
  Builder receipts account for approximately $1.02 across fourteen VMs,
  including commissioning and two real billing verification VMs. Each VM is
  counted once despite worker/reaper receipt duplication.
- Worker creation/deletion capture, immutable R2 upload/readback, independent
  cloud reaper capture/deletion, and absence of servers/volumes/primary IPs
  passed against real infrastructure. Local ledger records survive attempt
  cleanup; offsite copies inherit the bucket's 90-day lifecycle.
- Evergreen and Hub visibly label month-to-date amounts. Mixed periods do not
  produce a misleading monthly funding percentage. Typeahead's builder costs
  follow its declared ownership. Prefect retains its roughly $0.08/month backup
  storage estimate; the retired node has no continuing inventory charge.
- The existing Grafana **heavypad / host** dashboard has monthly counters,
  recent rates, a 1 TB budget reference, monitoring start time, sampler age,
  and Xfinity comparison guidance. The existing node exporter reads an atomic
  watchdog textfile; no additional listener remains. Existing host selectors
  were corrected after the migration. Live Prometheus retention is 90 days.
- Visual and live checks showed approximately 431 GB sampled Internet TCP,
  604 GB physical-host traffic, and a fresh sampler. Accounting began October
  17 UTC. Physical counters include LAN; neither series measures all household
  WAN traffic. Missing history cannot be reconstructed. The configured 1 TB is
  a budget reference, not an enforced household cap or ISP billing measurement.

The publication intentionally remains named **Incomplete** because fifteen
stopped Fly machine root filesystems have unmeasured charges. Other provider
invoice/operations/overage and household-meter limits remain explicit in
[the cost accounting notes](../costs.md). TypeSafe account-wide reconciliation
is deferred at Nate's request; the pre-existing Coral meter remains partial.

Validation: green CI at `8d0be2b` (840 tests, six skips, web checks clean),
focused accounting/watchdog tests, 54 Evergreen JavaScript tests, eleven Python
tests plus 47 subtests, and the Hub's Svelte/lint checks all passed.

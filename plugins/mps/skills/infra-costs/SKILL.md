---
name: infra-costs
description: Read or debug cost snapshots, provider coverage and resource ownership without confusing missing measurements with savings.
---

# infrastructure costs

Read [cost coverage and attribution](../../../../docs/costs.md) before comparing
snapshots or changing a connector. `flows/costs.py` owns collection and
publication; `packages/mps/src/mps/costs/` owns connectors and attribution.
The current schedule and runtime credentials are in `prefect.heavypad.yaml`.

Prefer the connected PDSX tools to read `io.zzstoatzz.cost.snapshot` records.
Resolve the account's current PDS if using raw HTTP. Snapshots contain
`lineItems`, not `items`; amounts are cents. Compare service-level line items,
coverage and pricing basis before interpreting provider/project total changes.
Coverage changes can look like spending changes.

Declared resource ownership takes precedence; name patterns are a fallback.
Inspect the current reconciliation code rather than maintaining a provider or
server inventory in this skill. Missing measurements are unknown, not zero.
`Incomplete` is a named completed state for known coverage gaps; connector
failures and partial publication have different contracts in the flow.

Before retiring resources or lowering backup retention, establish current
consumers and data ownership using [service retirement](../../../../docs/service-retirement.md).
Do not infer unused storage from a lack of restores. Confirm current CLI/API
behavior before treating a historical provider bug as an active limitation.

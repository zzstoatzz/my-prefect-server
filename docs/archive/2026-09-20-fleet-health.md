# Fleet health follows Evergreen's inventory

The deployed quarter-hour fleet sweep had seven hardcoded URLs plus Stream's
deep check, while Evergreen published 47 endpoints. A completed run at package
pin `abbd167` confirmed the mismatch; older documentation describing full
inventory coverage was not evidence of current deployment coverage.

The existing `fleet-health` deployment now reads Evergreen's public inventory and
Worker `/status` report. It requires a fresh, complete, nonduplicated set of
results with matching project/name/URL identities and consistent HTTP verdicts.
Unmonitored projects are not reported as healthy. An unhealthy endpoint becomes
the existing `fleet-health.unhealthy` finding; missing or invalid monitor data
fails the sweep after retries. No new deployment or notification route is added.

Stream's deep check remains. Jetstream, hub cost data, and relay-eval's latest
result remain supplemental checks because their URLs are outside Evergreen's
inventory. Evergreen owns the rest of the endpoint selection, including its
Typeahead serving-freshness check and separate zlay ingest/readiness checks.

Validation covers HTTP 503 findings, inconsistent HTTP verdicts, inventory drift,
duplicate/missing results, invalid or stale timestamps, and actual local HTTP
transport. Deployment verification must compare the active package pin and a
completed run's artifact/log coverage with the public inventory.

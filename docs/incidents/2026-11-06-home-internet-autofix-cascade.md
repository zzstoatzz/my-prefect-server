# Home internet outage and autofix suspension — November 6, 2026

Autofix is disabled at the operator's request. Do not re-enable it merely because
internet access returns or an individual flow is repaired. This incident exposed
a common dependency failure that automatic per-run diagnosis amplified.

## What happened

Xfinity reported planned maintenance affecting home internet until approximately
06:00 Central on November 6. HeavyPad remained powered on. Tailscale last saw it
at 01:07:37 Central; SSH timed out and the public Prefect endpoint returned
Cloudflare HTTP 530 / error 1033 (no connected tunnel). The operator confirmed
that another device on home Wi-Fi could not access the internet.

HeavyPad's workers use a local Prefect API, so losing WAN connectivity does not
stop schedules or local orchestration. Runs can still start and fail when they
fetch source, resolve dependencies, call external services or publish results.
A common network outage therefore produces failures across unrelated deployments.

At 01:24 Central, Discord showed automatic diagnoses for `strata-hourly`,
`bisk-snapshot`, `watch-stream` and `watch-fastmcp`, all reporting:
“Pi inference did not complete; retained usage is partial.” Diagnosis itself
needed unavailable external inference. The result was additional failed work and
repeated alerts, without evidence that those deployments needed code fixes.
This is a failure-amplification mechanism; the exact number of attempts and cost
were not measured.

Example evidence:

- [Strata failure](https://prefect-server.waow.tech/runs/flow-run/820357fc-98e3-4b88-97e7-786371a5d6d3)
  and [diagnosis](https://prefect-server.waow.tech/runs/flow-run/2656de38-3f36-4963-bb78-dfe157789582).
- [Bisk failure](https://prefect-server.waow.tech/runs/flow-run/06bb8eff-5a72-46bb-933d-954399452c76)
  and [diagnosis](https://prefect-server.waow.tech/runs/flow-run/9efc32f6-4a8e-4280-a0c9-bab0973abb9e).

The public API was reachable again by approximately 01:25 Central and accepted
shutdown operations. This proves a connectivity window, not completion of ISP
maintenance or uninterrupted recovery. Typeahead's cloud serving endpoint stayed
healthy with low replica lag, and Phi's public health check reported active polling.

## What was disabled

All seven live automations whose names contain `autofix` were disabled and read
back with `enabled=false`:

- `flow-run failure -> autofix`
- `autofix diagnosed -> discord`
- `autofix proposed -> discord`
- `autofix revised -> discord`
- `autofix proposed -> phi review`
- `autofix proposed -> merge-approved`
- `autofix revise requested -> autofix-revise`

The `autofix` and `autofix-revise` deployments were paused and read back as paused.
At the 02:27 Central follow-up, main registration had cleared those live-only
pauses. Both were paused again, and `paused: true` is now explicit on both
deployments in `prefect.heavypad.yaml` so registration preserves the suspension.
All seven autofix automations were still disabled; the check found no active
autofix runs.
The source already disabled the failure/review/merge/revision triggers; its three
remaining autofix notification automations are now also disabled in
`deploy/automations.yaml`. General flow-failure alerts and fleet alerts remain
separate from these autofix-specific notifications.

Pausing a deployment does not stop its active runs or necessarily prevent every
explicit invocation. Disabled triggers are the primary automatic-entry fence.
Existing history, code and pull requests are preserved.

## Cleanup error during mitigation

The client requested nonterminal runs by deployment name, but the deployed API
ignored that deployment filter. The cleanup therefore cancelled 129 unrelated
scheduled runs, including one Typeahead run waiting to retry. No run selected by
this cleanup was Running or Pending. This was an operator-tooling error, not an
ISP effect. Cancellation stamped start/end times on previously unstarted records;
those history entries must not be interpreted as actual execution.

Recovery identifies affected records by the exact mitigation message and excludes
the two autofix deployment IDs. Unstarted runs are restored to Scheduled at their
retained expected start times; the Typeahead run is restored to AwaitingRetry. All 129 unrelated runs were
restored and read back; a subsequent 500-run sample contained no unrelated
records still bearing the cancellation message. Fresh FastMCP, Stream-watcher
and Bisk completions were observed around 01:30 Central. Both autofix deployments
were still paused, and no nonterminal autofix runs appeared in that sample.
Do not trust server-side filters as mutation scope: assert each returned run's
full deployment ID and state locally before changing it. The existing deployment
verifier already documented this class of ignored-filter behavior.

## Conditions for reinstating autofix

These are design requirements for later work, not implemented protections:

1. Detect shared dependency outages before invoking an LLM. Use inexpensive,
   independent connectivity checks with hysteresis. Distinguish home WAN loss,
   Prefect reachability, provider failures and deployment-specific faults.
2. Group correlated failures into one incident. Existing deduplication by
   deployment, revision and error cannot consolidate a WAN failure spanning
   several deployments. Bound diagnosis concurrency, attempts and spend across
   the incident; send one incident update instead of one diagnosis per run.
3. Suppress diagnosis while its dependencies are unavailable. Recovery should
   require sustained health, then inspect whether the original failure persists.
   Do not propose code patches for infrastructure failures.
4. Declare recovery policy by workload. Coalesce obsolete monitoring samples;
   resume checkpointed collectors; explicitly retry important daily publications;
   preserve time-sensitive and event-triggered work for review. Do not blindly
   replay failed runs or old side effects.
5. Make notification recovery explicit. Verify delivery/retry behavior and avoid
   flooding Discord with historical failures when connectivity returns.
6. Exercise real failure modes before enabling: WAN loss with local API healthy,
   API loss, inference-provider loss, DNS failures, flapping connectivity, controller
   restart, and recovery with queued work. Verify no active workload is killed,
   no deliberately paused deployment is resumed, and no duplicate effect occurs.
7. Re-enable a small diagnosis-only cohort first, with an operator-approved budget
   and observed incident-level behavior. Restore proposal/review/revision/merge
   triggers separately after their own validation.

The home host remains a shared availability dependency. A local connectivity
controller cannot make it publicly reachable during an ISP outage. Off-host
monitoring or moving selected services requires a separate placement decision;
never activate a second scheduler against the same effects as implicit failover.

## Related context

- [Autofix behavior and history](../autofix.md)
- [Home-stack operations](../../deploy/heavypad-prefect/README.md)
- [Declared automations](../../deploy/automations.yaml)

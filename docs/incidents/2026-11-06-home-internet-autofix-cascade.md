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
those history entries must not be interpreted as actual execution. Although
Typeahead was Scheduled/AwaitingRetry rather than Running, it still had a live
in-process retry on its cloud VM. Cancelling that state interrupted its retry;
restoring the API state alone did not restore the process. This mitigation error
necessitated the fresh recovery build described below.

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

## Recovery verification after 02:27 Central

Main revision `8e96f503b70c9138fb9d9ea243d260e42ba72a40` passed CI and registered
at 02:38:54 Central. Read-back after registration confirmed both autofix
deployments remained paused, all seven autofix automations remained disabled,
and no active autofix runs were present in the checked sample.

Prefect API, PostgreSQL, Redis and services pods were Running without new restarts.
Home services, Dagster, workers and the bandwidth watchdog were active. Fresh
FastMCP watcher, Stream watcher, Bisk, identity, docs, fleet and diagnostics runs
completed; the checked 500-run sample had no overdue scheduled runs. The 02:00
costs run completed with its existing Incomplete coverage status. The 00:40
pub-search snapshot completed at 01:00, before the network outage.

The old Typeahead run `a097979a-3b8d-4323-b4f1-25f144d0c6bb` was stranded in
AwaitingRetry after its VM was deleted at 01:29. Its checkpoint freeze had failed
with `Remote policy is stale, future, or predates the verified checkpoint cut`.
Restoring its API state during the earlier cleanup did not restore the in-process
retry. The old attempt was marked Failed, and one idempotent fresh deployment run
was started with the existing qualified release pins:
[`66752af0-7ce0-4df2-a3e8-68c43dcb7abd`](https://prefect-server.waow.tech/runs/flow-run/66752af0-7ce0-4df2-a3e8-68c43dcb7abd).
The new execution is on a disposable cloud VM; it does not transfer the corpus
through the operator's laptop or hotspot. The fresh checkpoint exported successfully, completed full import/live catch-up,
and passed checkpoint freeze at 03:11:38 Central. Publication and read-back
verification completed at 03:22:53. The candidate installed, caught up and passed
qualification/promotion; the flow finished ordinary `Completed` on its first
attempt at approximately 03:58:58 Central. Public freshness then served
`b1793954044-cp-66752af07c`, with 7 seconds of lag, `stale=false`, and
`refresh_overdue=false`. The worker delivered artifacts and deleted the disposable
VM successfully at 03:59:19 Central. A later public check still served the new
build, with 11 seconds of lag and no freshness warnings.

The 02:48 fleet sweep completed but reported Pollz backend/freshness timeouts.
Both endpoints also timed out directly from the operator Mac; Fly reported the
separate `pollz-backend` app started with a critical health check. This finding
was unresolved at that check and did not show HeavyPad itself was unhealthy.
The 13:18 Central fleet sweep later returned HTTP 200 for Pollz backend,
freshness and frontend, with no unhealthy findings in the complete sweep.

Two failures predate or remain separately fenced from this recovery: the backup
restore verifier last succeeded October 27 and failed November 2; the isolated
chaos experiment refuses to restart until its previous failure is reviewed. Neither
was reset to make the status appear healthy.

## Temporary local workload pause for a noise report

At approximately 03:09 Central, the operator reported a winding-down/fan sound
and requested safely stopping work. New submissions were paused on these six
previously unpaused pools: `heavypad-pool`, `heavypad-background-pool`,
`heavypad-build-pool`, `heavypad-gardener-pool`, `heavypad-interactive-pool`, and
`heavypad-typeahead-pool`. No active workload was killed. Core services and the
separate cloud builder were left running. The operator then reported that the
sound had stopped and there was no unusual smell.

CPU package temperature was about 57 C, SSD temperatures were 36–38 C, and host
load was low. The CPU fan slowed from about 2,255 to 1,849 RPM. Both NVMe devices
passed read-only SMART health checks, with zero media/data-integrity errors, zero
error-log entries and no thermal warnings. Recent kernel logs contained no
matching storage, hardware or thermal errors. These observations support normal
fan spin-down but do not remotely identify the sound.

At 03:18 Central two later CI VMs under `spindle.service` were consuming CPU
independently of Prefect; CPU temperature rose to about 72 C. The systemd
`spindle.service` cgroup was frozen, preserving its processes while stopping CPU
work. Subsequent readings returned to 57–58 C and about 1,700 CPU-fan RPM.
Spindle/CI remained frozen until the explicit midday resume described below.

The final audit found that the API's pool `is_paused=true` flags did **not** stop
worker dispatch: watcher jobs continued completing after the pauses. Do not rely
on those flags alone on this deployed server. Worker controller processes were
stopped from polling, while the already-started transform and memory-synthesis
runs were allowed to finish. The six local worker services were then stopped
normally and read back `inactive/dead`:

- `prefect-heavypad-worker.service`
- `prefect-heavypad-worker@background.service`
- `prefect-heavypad-worker@build.service`
- `prefect-heavypad-worker@gardener.service`
- `prefect-heavypad-worker@interactive.service`
- `prefect-heavypad-worker@typeahead.service`

The controller/flow distinction matters: an AwaitingRetry or terminal API state
alone does not prove a flow process is absent. Preserve active flow lineages and
allow business work to finish before stopping the worker cgroup.

`prefect-home-registration.timer` was also stopped while CI was frozen, to avoid
repeated requests to the suspended CI service. The ordinary core services and
cloud builder were left running.

These holds caused the late-run backlog from approximately 04:00 Central onward;
it was not evidence that WAN connectivity remained unavailable. The consequences
of keeping workers stopped should have been made explicit at the time. The exact
inventory is saved privately on HeavyPad at
`~/.local/state/mps-recovery/2026-11-06-quiet.json`. Its later restoration marker
records `restored: true` and `current_holds: []`. This temporary hold is separate
from the durable autofix suspension.

## Explicit midday resume and outcome recovery

At approximately 12:44 Central, the operator requested cancelling the late work,
restarting workers and backfilling outcomes. Exactly 392 old `Scheduled/Late`
runs were cancelled in two passes (391 plus one scheduler race). Every mutation
re-read the full run ID, state type/name and expected start time locally. Future
scheduled runs, active work and waiting retries were preserved. Cancellation
retains the history; it does not reconstruct missed observations. The cancellation
inventory and per-outcome run ledger are retained privately on HeavyPad under
`~/.local/state/mps-recovery/2026-11-06-resume/` as `cancelled.json` and
`backfill-runs.json`.

The six recorded worker services and their pools were resumed, Spindle was
thawed, and the registration timer was restarted. All six pools subsequently
reported `READY` and unpaused; all six services reported active. Autofix's seven
automations and two paused deployments were excluded from the resume.

Recovery exposed a second blocker: Redis had reached its 192 MiB `maxmemory`
with `noeviction`, causing broker errors and HTTP 500 responses while acquiring
the analytics writer lease. The cap was raised to 384 MiB within the existing
512 MiB container limit, after publishing the manifest change on both main
remotes (`ec453c46d468495edbf742e6eb16a9ac810ef4c0`). Synchronous AOF, the existing
PVC and `noeviction` were preserved; no keys or streams were flushed. A real
`analytics-duckdb-writer` lease acquired and released successfully. Event replay
peaked at about 316 MiB, then fell to about 65 MiB after consumers drained it.
Main registration subsequently registered that exact green revision.

The first ingest and warehouse attempts failed during the Redis exhaustion;
one idempotently keyed repair of each completed after the fix. Current-output
publishers and checkpointed collectors were coalesced into fresh runs rather
than replaying every obsolete schedule:

- [Ingest](https://prefect-server.waow.tech/runs/flow-run/24c34b67-aee3-4d3e-bfcb-01bca8c396b7)
  and [warehouse](https://prefect-server.waow.tech/runs/flow-run/214dc8f5-b37c-450b-bf34-718be4991a1a)
  completed. Email classification, Dagster-backed transform, memory synthesis
  and their brief follow-ups completed through the ordinary dependency chain.
- Bisk, Bufo traffic, AT Protocol docs, Evergreen, identity, fleet diagnostics
  and MCP Atlas completed fresh runs. Ordinary watcher schedules resumed.
  [Leaflet atlas](https://prefect-server.waow.tech/runs/flow-run/6c6ba2af-2ff9-4b60-acb1-733a9f035ca6)
  rebuilt 102,160 points and deployed successfully at 13:25:42 Central. Public
  read-back from HeavyPad confirmed that point count and generation timestamp
  `2026-11-06T18:51:13Z`. Its
  optional facts refresh exhausted its retry on a Turso read timeout; the flow
  preserved committed `facts.json` while publishing the rebuilt atlas and its
  fresh summaries.
- FastMCP's watcher ran once with a reset watermark to re-examine unread
  notifications after broker failures; normal thread/update deduplication was
  retained. One 12-hour brief covered the gap rather than replaying missed briefs.
- [Email triage](https://prefect-server.waow.tech/runs/flow-run/b8b41e56-b26d-4dd5-a7a6-1dd44bcfb583)
  delivered its shortlist and entered the expected human-response pause. No
  operator approval or email action was fabricated to complete that workflow.

Phi's separate Fly machine also became unresponsive: its public health check and
machine exec timed out, and the first scout/editorial requests failed. The
existing machine was restarted with the same image and persistent volume,
following the bot's operational guide. Its health check returned healthy with
active polling. Each missed pass was retried once. Logfire confirmed the actual
`chicken scout` span completed at 13:10:37 Central and `editorial` at 13:14:18,
both without exceptions, including their final summaries. Prefect trigger
acceptance alone would not prove this, because Phi executes these callbacks in
the background and catches their exceptions. Tag maintenance completed afterward.
The [Phi atlas](https://prefect-server.waow.tech/runs/flow-run/b0376781-f11b-4866-a255-9117a46e811e)
then published 10,853 points to its PDS and archived the JSON at 13:22 Central.
Its ordinary completion trigger started
[Docket](https://prefect-server.waow.tech/runs/flow-run/f4449fd0-018b-49c6-abd1-e0a94ea08a08),
which published six synthesized items and completed its archive at 13:34:56.

Strata's first recovery pass saw 6,252 changed or missing sealed-segment
summaries and read its default 2,000-segment budget. An ordinary resumed hourly
run (`b3d27f81-ab5d-4e96-a637-d26115811929`) read another 2,000. The
[final bounded pass](https://prefect-server.waow.tech/runs/flow-run/af6935fa-d539-4cb9-9e64-3c620fb7fe74)
used `max_segments=10000` and completed at 13:45:48 Central. Its artifact records
7,764 sealed segments, 2,252 needed, 2,252 ingested and zero skipped; publication
updated 268 objects. This closes the backlog seen at that listing without
replaying old hourly publications or chasing future live changes indefinitely.

Final checks at approximately 13:47 Central found all six pools ready and
unpaused, all six workers and the registration timer active, no `Late` runs,
all seven autofix automations disabled and both autofix deployments paused.
Phi was healthy and polling; Typeahead still served the recovered build with
healthy freshness while its independent noon cloud refresh proceeded normally.
Redis AOF writes were healthy and memory was well below the 384 MiB cap.

The cancelled health/backlog samples cannot be recovered as historical
measurements. Current observations and publications resume on the ordinary
schedule. The pre-existing backup-verifier failure and chaos-test review latch
were not reset. Autofix remains disabled until a separately approved design and
reinstatement.

## Related context

- [Autofix behavior and history](../autofix.md)
- [Home-stack operations](../../deploy/heavypad-prefect/README.md)
- [Declared automations](../../deploy/automations.yaml)

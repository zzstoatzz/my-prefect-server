# Scoped HeavyPad migration

Nate approved batches 1, 2, 4, 5 and 8: 21 deployments. Long publishers,
Gardener/coding, Typeahead, canonical routing, node deletion and broad source
reorganization are outside this goal. Preserve existing unrelated checkout work.

| Batch | Deployments | Status |
| --- | --- | --- |
| 1 | phi-curation, phi-likes-review, phi-editorial, phi-character-retro, phi-chicken-scout, phi-chicken-precheck | Migrated: six validation runs passed, all target schedules active, original clocks empty. |
| 2 | watch-fastmcp, fastmcp-brief | Original owner; preserve both Variables, six-hour recent event window and briefing automations. |
| 4 | strata-hourly, phi-tag-maintenance, phi-atlas, docket | Original owner; preserve archive/checkpoint files and shared writer coordination. |
| 5 | ingest, classify-emails, transform, brief, phi-memory-synthesis | Original owner; transfer as one shared-writer cohort including Dagster's lock client. |
| 8 | email-backlog, email-triage, phone-presence, stream-admission | Original owner; reconcile paused email approval and interactive callers. |

## Phi qualification

Source `eaa4680cd97e60ef27254db2dfbc1b90d7d780ca` adds `validate_only` to the
existing phi-trigger flow. It reads live bot health and sends an authenticated
unknown-slot request. The bot checks its token before returning its supported
slots and cannot schedule a known bot pass on this route. Each target slot must
be advertised. Validation completes with state type COMPLETED/name Validated;
it proves deployment execution, token loading, network, health and API contract,
not completion of an editorial/market pass. Real scheduled behavior remains due
on its original cadence; no weekly/monthly/deadline pass was replayed for testing.

| Slot | Original deployment | HeavyPad deployment | Completed validation run |
| --- | --- | --- | --- |
| curation | ca4af159-74b1-495d-b226-86ea82f5015b | af3781c0-020f-45eb-90de-801aca6e9786 | c6d4191c-7bcc-4c3d-8843-d13349bd99cf |
| likes-review | 1b210402-2c7e-474c-9340-9eb732c29425 | ef81bea3-f5f6-4e9e-8d02-616571fdfdd1 | 504d6fca-0399-447b-8ef6-4e58d5da3b9d |
| editorial | 49461499-3c20-422b-aba1-5f3d9a8955b1 | 7056695e-b9b4-4729-b1d4-5b67a1fdb3b2 | 15721422-6b84-4dce-9405-417d82181830 |
| character-retro | ec2b334a-5292-476b-aa8e-d58fa7b68752 | cabf0900-4a3f-4137-ae8c-df5bacc46e5d | fee6ffa9-fd55-4bf6-8ddc-1403cbdfff14 |
| chicken-scout | ec556fa6-c4e3-4684-8d92-6ac7c69e26af | 9bc72ce6-5adb-43c5-87ed-9e6a48329176 | 7476f14e-1ced-4460-9bbc-49bb4fc92aa7 |
| chicken-precheck | c6200903-8f8a-41a0-8492-612464c78256 | 3b13d70f-bd6f-458f-925a-d7329db1149a | 7558dd1f-9824-4bf1-94a6-4fc8b1330029 |

The original schedules were emptied in source `58b46ac` and explicitly cleared
live. Readback found six empty schedule lists and zero old Scheduled, Pending,
Running, Paused or Cancelling runs for these six deployment IDs. HeavyPad
activation source is `68359e03a26e341fc1e1749571078429e36d7f35`, retaining the
original UTC cron expressions and adding per-deployment concurrency one/CANCEL_NEW.
The known email-triage paused run remains untouched.

The Phi token matches canonical SOPS and original Prefect. Target drift was
introduced while its schedules were inactive, detected, and repaired. Rotation
remains manual/unperformed; the secret-store README describes the issuer and
consumer sequence. Existing HeavyPad failure notifications and target run links
were verified with no synthetic Discord message. Automatic Gardener diagnosis
remains on the original server, as before; its migration is outside this goal.
No Grafana alerting was added.

Rollback: clear selected HeavyPad schedules, cancel unstarted queued runs and
wait for active execution to finish. Restore only those original source crons,
register the selected original deployment(s), and verify one owner before resume.
Keep history and the shared token; never replay missed market slots as catch-up.

Atlas's first natural run `6471ce1e-ddb2-4ee2-9b56-fe682f74629e` completed
October 27 at 00:45:10.635151 UTC, closing the prior batch's pending observation.

Live target readback confirmed all six active UTC cron schedules and future
automatic runs with ordinary slot parameters (no `validate_only` override).
The next chicken precheck is run `93f5fccb-5f43-4237-9038-6196bee8ff09` at
October 27 04:00 UTC. No scheduled pass has fired yet after this cutover.

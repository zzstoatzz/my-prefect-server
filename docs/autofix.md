# autofix

Autofix and autofix-revise are deliberately paused following the November 6
home-internet outage. The failure, proposal, review and revise triggers are
disabled. See the [incident and reinstatement requirements](incidents/2026-11-06-home-internet-autofix-cascade.md).
Registration or worker repair must not resume this chain.

## retained execution paths

The current definitions are in [prefect.heavypad.yaml](../prefect.heavypad.yaml).
Autofix's home runner remains an immutable wheel; its policy source follows the
registered main pin. Pi-agent has its own pinned source and wheel. Updating
checkout code does not replace either runner artifact. Preserve each deployment's
delivery mode when making a separately authorized change.

The suspended chain includes `autofix-revise`, `phi-pull-review`,
`merge-approved` and its `test-pull-patch` child. The separate exe worker was
inactive and disabled at the November 15 audit. The pull-comment bridge and
hourly pull watcher still observe events; that does not mean a revision job
will start. Decide their continued use with the chain's consumers.

## boundaries to preserve

Failure logs and incoming comments are untrusted evidence. Screening receives
the trusted operation separately. A shared network or provider outage must not
fan out into concurrent diagnoses of unrelated repositories. Repeated evidence
must not create an unbounded retry or proposal loop, and the diagnostic flow's
own failures must not recursively trigger another diagnosis.

Patch preparation and merge are distinct operations. A proposed patch or Phi
review does not authorize production changes. `merge-approved` requires human
approval, rechecks the reviewed revision and uses the test child before merging.
Changes to deployment configuration, agent policy and credential-handling code
must remain visible in its protected-path summary. The current implementation
is in [merge_approved.py](../flows/merge_approved.py); deployment pause state is
not a substitute for those application boundaries.

State name matters independently of type. `Diagnosed`, `Degraded` and `Skipped`
can be completed states; none alone proves a shipped fix or resolved incident.
Use [coding-job evidence](coding-jobs.md) to connect runs, reviews and delivered
changes without treating missing telemetry as zero spend.

## replacement work

[Alert dispatch with Albedo](alert-dispatch.md) distinguishes the manual
`albedo-alert-check` pilot from the proposed automatic dispatch/approval loop.
That pilot does not reinstate autofix or grant deployment authority.

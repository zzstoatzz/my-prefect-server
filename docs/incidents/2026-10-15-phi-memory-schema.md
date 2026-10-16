# Phi memory synthesis and legacy namespace schemas

Phi memory synthesis failed on 12 consecutive hourly runs beginning at
2026-10-15 16:02 UTC (11:02 a.m. Central). TurboPuffer rejected the likes
reconciler's observation filter with `filter error in key kind: attribute not found`.
The mitigation is commit `3b1dd680f173b5b612fa35a8717b486c43b7b391`, published
to both main remotes and registered on `home-pool`.

## Evidence and cause

The [last successful run](https://prefect-server.waow.tech/runs/flow-run/6d4b7239-589c-4147-9fd8-1e523e2e736a)
and [first failure](https://prefect-server.waow.tech/runs/flow-run/4132148a-81d0-4a2e-9acb-cbb3855d4929)
both used deployment version `9e3a8fb9` and flow version
`d951b7a16e3d9276a1aa4eb50387f119`. The
[latest failure inspected](https://prefect-server.waow.tech/runs/flow-run/511bbd0e-c80c-49a2-9705-b46a3b812c6b)
traces directly to the ANN query in `write_likes_observations_to_turbopuffer`.
This was a latent data compatibility defect, not a new release at the onset.

A read-only audit of all 201 `phi-users-*` namespaces found two without `kind`:
`phi-users-stoatmom_bsky_social` and `phi-users-void_comind_network`. Each held six
legacy `type=conversation` rows. Fourteen other namespaces had `kind` but no
`status`; at least one contained real observations that must remain eligible.
The historical logs do not identify which of the two legacy namespaces triggered
the first failure, so that attribution remains unproven.

Commit `80cef2f` on October 3 added unconditional neighbour reconciliation before
the observation write. The write supplies the current schema, but the preceding
read assumed that schema already existed. Existing namespaces are not necessarily
current namespaces. TurboPuffer [maintains schema per namespace](https://turbopuffer.com/docs/write);
a missing schema attribute is different from a null value on a row.

The extraction-stage reader compounded this: its broad `except Exception` turned
schema failures and service failures into `no prior knowledge`. Extraction could
therefore proceed with false absence evidence before the write-stage query failed.
The original tests exercised model decisions and filter literals, not queries
against different live schema generations. Fifteen reported live model evaluations
in the introducing commit likewise did not establish database compatibility.

## Mitigation and validation

Both readers now use one schema-aware query helper. Missing namespaces and namespaces
without typed observations have no reconciliation targets. A namespace without
`status` uses the observation-kind filter alone; current namespaces exclude
superseded observations. Attribute projections tolerate older namespaces. Only
namespace-not-found is converted to an empty result; other API errors propagate,
and the extraction read now has task retries.

No blanket schema migration or relabeling of old conversations was performed.
The ordinary observation write still establishes its schema when new observations
are added. Legacy conversation text is not promoted into factual observations.

Validation: `just check` passed (655 tests, three credential-gated skips, lint,
formatting, types, deployment contracts, Svelte checks, and frontend lint).
All three gated tests passed separately against disposable real TurboPuffer
namespaces, covering both query rankings, absent namespaces, conversation-only
schemas, missing status/tags, null-status observations, superseded rows, and an
invalid query that must raise. The fixtures delete only their own unique namespaces.
Read-only checks on the three production examples returned respectively zero,
zero, and two eligible observations.

Mitigation CI passed on `3b1dd68`, including registration. The
[production verification run](https://prefect-server.waow.tech/runs/flow-run/d71ef823-6880-43fc-a501-5ce3e2b414f0)
completed at 2026-10-16 03:27:12 UTC on deployment version `3b1dd680`: 122 summaries,
34 extracted observations, 14 actionable candidates, 12 reconciliation NOOPs,
and two ADDs. A subsequent read confirmed two observations now coexist with all
six legacy conversations in `phi-users-void_comind_network`; the other legacy
namespace still contains its six conversations and no observations. This verifies
the previously incompatible write path, not only an empty or skipped run.

## Adversarial review after deployment

The mitigation addresses the observed failure, not the entire memory write contract.

- **High at the mitigation revision: invalid model targets could supersede unrelated memory.**
  `plan_observation_write` falls back to `existing[:1]` when an UPDATE or DELETE
  names no valid targets. Calling the real function with either `targets=[]` or
  `targets=[99]` and one unrelated neighbour selects that neighbour for replacement.
  An existing test explicitly expects this behavior. Reject invalid targets before
  any write; geometric proximity is not replacement authority. This is an
  independently reproduced defect, not evidence of damage in these failed runs.
- **Medium: replacement is not atomic.** The new row is upserted and old rows are
  superseded in separate API calls. Failure between them leaves both active;
  later observations in the batch are not processed. Blindly adding whole-batch
  retries is insufficient because reconciliation may make a different decision
  against the partially changed data. Use a single namespace write for the paired
  mutations and define replay behavior at observation granularity.
- **Medium: one author still stops the whole batch.** Genuine read failures now
  remain visible, but all likes observations still join in one writer task. Per-author
  outcomes would allow independent work to finish while reporting incomplete work
  explicitly. The fixed shared snapshot path and lack of a deployment concurrency
  limit also mean overlapping runs are not isolated.
- **Schema ownership remains split.** The bot, summary writer, and likes writer
  share namespaces with different schema assumptions. The summary writer still
  declares a schema without `status`, so partial schemas are not exclusively
  historical. This patch handles absent
  `kind`/`status`; it does not repair wrong types, disabled indexes, or migrate
  legacy conversation data. A schema contract tested by both repositories is the
  next preventive change. Metadata and query are separate calls, so concurrent
  writes are not a transactional snapshot.

The new live regressions are credential-gated and skip in ordinary CI. They were
executed for this release; continued coverage requires making that live matrix an
explicit release check for memory-reader changes. Passing the default suite alone
still does not establish compatibility with production schemas.

The root failure was introducing a read-before-write dependency without accounting
for historical schemas, then validating model semantics instead of the storage
boundary. More retries would not have repaired the deterministic HTTP 400.

## Replacement target validation follow-up

`plan_observation_write` now rejects UPDATE and DELETE decisions with no targets
or with any target outside the supplied candidate set. Mixed valid and invalid
lists are rejected as a whole. Target values must be integers without coercion
from booleans, strings, or floats. Valid duplicate targets are deduplicated while
preserving the model's order; the planner never invents a replacement target.
Unknown actions also raise instead of silently becoming ADD.

The writer invokes the planner before its observation upsert and supersede patch.
Invalid decisions therefore raise before either memory mutation for that
observation. They are not converted into successful NOOPs or automatically retried
as a whole batch. Previously processed observations in the batch remain committed;
atomic replacement and per-observation replay remain separate follow-ups.

Regression tests call the actual planner and Pydantic validation directly,
without a model or database. A separate direct check confirmed omitted targets
are rejected for both actions. Coverage includes empty, zero, negative, out-of-range,
and mixed targets; an empty candidate set; coercion attempts; valid non-nearest
and multiple targets; duplicate targets; and preservation of input evidence after
rejection. The old test that expected nearest-neighbour fallback was removed.
This enforces target validity, not factual correctness: a model can still explicitly
select the wrong valid observation, which requires separate semantic evaluation.

The separate bot repository also retains `return picked or list(existing[:1])`
in `src/bot/memory/extraction.py:reconciliation_targets` on inspected main
`ce0dac4`. Its `_reconcile_observation` calls that helper before replacement.
The Prefect fix does not change or deploy that independent writer; removing its
fallback is a separate necessary fix before claiming this protection across all
Phi memory writers.

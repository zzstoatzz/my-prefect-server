# Coding-job evidence

Run `just coding-jobs` to generate a private Markdown and JSON inventory under
`~/.local/state/mps/coding-jobs`. Pass a directory to keep a separate snapshot:
`just coding-jobs /tmp/coding-jobs-audit`.

The command reads the configured Prefect server and the public PDS records of
Gardener, Phi, and the operator. It does not run jobs or change external state.
It inventories the named coding flows, test/probe flows, and the deployed
`phi-pull-review` runs, including cancelled and failed attempts. `dep-bump` is
included as a separate flow when present. Add new flow names to `FLOWS` in
`scripts/coding_jobs_audit.py` when introducing another execution path.

Each run links to Prefect and records state **name** separately from state type,
log and artifact coverage, source incident/run ID, pull references found in
logs or parameters, policy rejections, and structured Pi tool/usage counts.
Raw prompts, credentials, log bodies, artifact bodies, and deployment job
variables are not exported. The output files have mode 0600.

The pull inventory includes all Gardener and Phi pulls, including personality
edits that did not run through Pi. This is supporting evidence, not a count of
isolated coding jobs. Review verdicts must match the pull's current CID and round.
An absent owner status is **unknown**, not open or unmerged. The appview can lag;
verify delivered changes against Git. `git patch-id --stable` can compare a
published patch with the eventual commit even when commit IDs differ.

## Reading the results

- `Completed` means the flow finished. It does not establish a patch, merge,
  incident resolution, or useful work. `Diagnosed` and `Degraded` are distinct
  completed-state names; retain that distinction.
- A patch artifact or published pull proves an inspectable proposal. A review
  proves feedback on that revision. Neither establishes a shipped fix.
- A Git match proves inclusion in the inspected history. Deployment and incident
  recovery require their own evidence; a diagnosis alone does not prove either.
- Missing usage is unknown spend, not zero. Coverage counts accompany totals;
  historical cost or time saved cannot be reconstructed from missing records.
- Prefect filters page to an empty result, rather than treating a short page as
  complete. Errors and repeated pages fail the command instead of writing a
  seemingly complete new report. Reads are not a transactional snapshot; an
  active run can change during collection.
- Deleted runs and direct shell invocations cannot be recovered from Prefect.
  Durable PDS pulls can outlive their originating runs. New flow names and other
  author identities need explicit inclusion.

## What supervises what

The worker owns process launch, cancellation, timeout and exit/log evidence.
The Python flow prepares the repository, invokes Pi and publishes the result
using credentials outside Pi's sandbox. These are separate responsibilities.
Use the deployment's actual pool and artifact to identify which runtime handled
a run; historical Sprite evidence does not describe the current exe worker.

## Usage collection

Both the local Pi caller and the isolated caller consume Pi's JSON event stream.
Each assistant `message_end` records input, output, cache-read, and cache-write
usage through `mps.spend`; partial deltas are not counted. Pi's disjoint input
buckets are converted to the inclusive input total expected by genai-prices.
The model catalog supplies an **estimate**, with its pricing model recorded.
Pi's configured zero price is not accepted as proof of free inference.
Unknown pricing remains null, including after analytics import. Subscription
allocation, provider billing adjustments, and isolated-worker compute are not included.

`llm_usage` records are emitted to Prefect logs as responses complete, as well
as the existing local spend JSONL. This also exports the prompt judge's usage,
which previously could disappear with an ephemeral worker. Each record has a
unique ID for deduplication, the flow-run ID, task, provider/model, tokens,
estimated cost, and pricing basis. Arbitrary caller metadata is excluded from
remote logs. Raw Pi events, prompts, and tool results are not telemetry.

Each Pi invocation also emits a `pi_execution` summary and a
`pi-execution-usage` table artifact with duration, outcome, message/usage counts,
known estimated cost, and missing-usage/unpriced counts. Failed processes and
timeouts preserve completed responses and label coverage incomplete. A hard
kill or host loss can prevent the final artifact; already-sent usage logs remain
available subject to Prefect delivery and retention. A provider response that
never returns usage cannot be priced from this evidence.

Use `just coding-jobs /tmp/coding-jobs --spend-log /path/to/recovered.jsonl` to
join historical local spend records with API logs. The join uses full run IDs
and deduplicates by spend event ID. Old zero-price sentinels count as unknown.
The report separates judge-only coverage from Pi execution coverage and exposes
known estimates without pretending they are whole-job bills.

Deployments pinned to older wheels or Git revisions do not gain this behavior
from a source change alone. Preserve their execution contracts when applying
telemetry backports; do not migrate a home-worker job into an isolated VM incidentally.

The private worker release directories referenced in `prefect.heavypad.yaml` contain
`provenance.json` and `source-overlay.tar.gz` beside each telemetry backport
wheel. The provenance records the original Git revision or original wheel hash;
the overlay contains only the modified modules.

The default report command also loads `recovered-spend.jsonl` from its output
directory when present. Jobs tagged `telemetry-proof` are labeled as probes.
Review-bot inference outside these flows and infrastructure billing require
separate attribution; these reports do not claim an all-in bill.

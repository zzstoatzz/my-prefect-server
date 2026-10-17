# Repository consolidation, October 17, 2026

The primary checkout had been left on `codex/dagster-hub`, while production's
ordinary Git-backed Prefect deployments already used main at `523b0fe`. There
were 20 registered worktrees (one missing), 29 local branches, and additional
remote-only branches. Several live services had source changes only on side
branches. Main is now the integration and operator checkout.

## Recovery archive

Before changing or deleting worktrees, the operator's machine saved a private
archive at `~/.local/share/repo-archives/my-prefect-server-20261017/`:

- `all-refs.bundle`: complete Git history and local/remote refs, verified with
  `git bundle verify`.
- `refs.txt`, `worktrees.txt`, `git-config.txt`: original refs, paths and config.
- `manifest.tsv`: maps original worktree paths to snapshot filenames.
- `<id>.tar.gz`: source, untracked files and non-cache ignored files for each
  existing checkout, including dirty work and local operator notes.
- `<id>.patch`: tracked changes against each checkout's HEAD.

Virtual environments, node_modules, bytecode, lint/test caches and Svelte build
caches were omitted. Archives may contain private configuration; keep the
directory private and do not upload it to Git. No archive branch is needed:
the bundle preserves every original branch independently.

Restore into a separate temporary directory with `git clone <archive>/all-refs.bundle
<destination>`, inspect `refs.txt` for the desired original ref, and extract that
checkout's source snapshot. The archive is a recovery mechanism, not another
working checkout or deployment source.

## Retained production behavior

- Bandwidth watchdog: integrated the deployed service, budget enforcement,
  notification delivery verification, tests and operator commands.
- Private inbox review: integrated the running Gmail/Proton service and its
  authenticated capability fix. Credentials and mailbox state remain on the host;
  consolidation does not authorize mailbox writes.
- Email backlog: integrated the already-registered on-demand deployment, full
  unread/Sent review, explicit verified mark-read batches and documentation.
- Redis durability: integrated the production persistent Redis manifest,
  two-webserver setting, migration tooling and verified rollout evidence. This
  does not rerun the migration or replace a live database.
- Dagster spend import: retained the deployed native bulk importer, timestamp
  validation, replay/duplicate regressions, CPU affinity and benchmark evidence.
  Historical branch changes to Typeahead and cost ownership were considered
  separately rather than replacing newer main code wholesale.

## Archived ideas and superseded work

| Former branch/worktree | Disposition and useful context |
| --- | --- |
| `codex/dagster-hub`, `codex/strata-heavypad` | Main already has the Strata snapshot work and subsequent quarantine/publication fixes. The old unique commit is an earlier version, not a missing feature. |
| `codex/evergreen-health`, `codex/hub-project-inventory` | Superseded health/inventory experiments; do not restore the duplicate health deployment or obsolete Hub redesign. Mobile notification formatting and explicit project ownership remain useful design references. |
| `fleet-http0` | Optional Evergreen retry/transport diagnostics schema and display improvements. Revisit against the proxy's actual response contract; unrelated to the October 17 plyr worker stall. |
| `codex/bulk-spend-import` | Deployed importer retained. Explicit resource ownership and missing-cost semantics remain useful follow-up work; review current inventory before adopting historical declarations. Typeahead supervision overlaps the active compaction work. |
| `codex/pinned-skill-delivery` | Earlier pinned skill-export design; main uses the later verified MCP skill delivery. Do not replace newer autofix behavior with this old branch. |
| `codex/instance-operations-cleanup` | Useful chaos runbook and repository ownership explanation. Reconcile against the current server repository before reviving old command examples. |
| `codex/llm-spend-dashboard` | Old dashboard, Semble adoption and image-pinning experiments; compare with current curation and deployment behavior before reuse. |
| `codex/operator-docs-cleanup` | Simulator lifecycle work subsequently landed with additional zombie-process handling; old operator inventory is historical. |
| `codex/reconciliation-checkpoint-20260911` | Older dirty-checkout checkpoint, retained only for forensic recovery. |
| `gardener-judge-and-tests` | Historical prompt screening change; compare against current execution-policy and isolation code rather than reapplying blindly. |
| `work-update` (dirty) | Proposed `work.update` events linking diagnosis, pull creation and agent failure to the original failed run. Useful for an operator-facing incident timeline; requires adaptation to current autofix and real client verification. |
| `.cloud-bootstrap` (dirty) | Proposed cloud bootstrap, API wrapper and secret/config routing. Preserve as a design reference; do not overwrite current credentials or production access paths. |
| `codex/gardener-identity-live`, `phi-agentic-workflows`, `zig-toolchain` | Historical remote-only experiments; retained in the bundle. Current Gardener execution and toolchain configuration supersede these paths. |
| `fastmcp-backlog`, `llm-cost`, `codex/gardener-mcp-skills`, `fix/phi-memory-schema`, `main` in `-strata`, `minnow`, `sablefish`, other contained branches | Commits already reachable from main; redundant checkouts. |

The primary checkout's uncommitted cached-pull-step notes and HeavyPad GPU and
bandwidth audits are preserved in its snapshot and `primary-dirty/`. They are
historical evidence, not newly verified production requirements.

## Fleet incident

On October 17 at 04:37 UTC, plyr's worker still heartbeated but had stopped
consuming more than 400 queued tasks after Redis connection errors. Its stuck
upload reaper was itself overdue. Restarting the worker restored consumption;
three abandoned uploads were failed by the existing reaper. At 04:39 UTC all 63
fleet checks passed, and at 05:00 UTC queue progress and freshness remained
healthy. A fresh upload was not performed as part of that verification.
The new production Docket canary `06ad3024-413b-70e7-8000-1daa6981f3cc`
(`sleep(0)`) subsequently completed on the restarted worker, proving new-task
consumption independently of heartbeats.

The operational follow-up is
[plyr.fm issue 2168](https://github.com/zzstoatzz/plyr.fm/issues/2168): detect
stalled consumption independently of heartbeats, present an explicit scoped
recovery action in the remote session, execute it after approval, and verify
recovery. Fleet findings currently notify; they do not automatically invoke
the failed-flow autofix route.

## Consolidation verification

`just check` passed: deployment contracts, Ruff, formatting, types, 735 Python
tests (three existing opt-in skips), Svelte checks and frontend lint. The inbox
extra is now installed for local and CI type/test checks. SHA-256 comparisons
confirmed the retained importer, inbox provider/server and bandwidth watchdog
source matches the active HeavyPad services. The live cluster has the persistent
Redis deployment and two API pods described by the retained configuration.

Immutable wheel/image deployments retain their declared loading modes and tested
artifact pins. Updating the ordinary Git-backed deployments to a main commit
must not silently replace those artifacts with incompatible current source.

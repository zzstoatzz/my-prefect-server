You are auditing documentation drift for a home server called heavypad. This is a read-only audit: do not modify anything under my-prefect-server/, do not run git commands that write, and do not try to reach any host other than your model provider.

Inputs in the current directory:
- units.tsv: a snapshot of heavypad systemd user units (name, load, active, sub, description), taken just now.
- unit-paths.tsv: where each installed unit file lives on heavypad.
- my-prefect-server/: a fresh clone of the repo that is meant to own heavypad deployments. Unit sources live under deploy/*/ (look for *.service, *.timer, install.sh, README.md); prose lives in docs/ (especially docs/operations.md, docs/monitoring.md) and AGENTS.md.

Produce report.md in the current directory with:
1. Untracked: running or installed units with no source under deploy/ in the repo. For each, the unit name, its description, and the closest related file in the repo if any.
2. Tracked but absent: units the repo defines or documents that are not in units.tsv, or that are not active.
3. Doc drift: specific claims in docs/ or deploy/*/README.md about heavypad services (ports, names, schedules, what runs where) that contradict units.tsv. Quote the file path and line.
4. A short prioritized list (at most 5) of the fixes that would matter most.

Be concrete and cite file paths. Say "unknown" rather than guessing. Keep report.md under 150 lines.

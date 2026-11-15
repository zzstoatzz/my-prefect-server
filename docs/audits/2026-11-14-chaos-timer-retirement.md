# Hourly chaos lab timer retired

`prefect-chaos.timer` and `prefect-chaos.service` were removed from HeavyPad on
2026-11-14. The timer ran the prefect-server chaos lab every 45 to 90 minutes
against a disposable compose stack.

## Why

- It had not run an experiment since 2026-11-10. A `redis-restart` experiment
  that day passed its fault check (recovery in 9.9 s) and failed the baseline
  latency check afterwards (p95 94.5 ms against 50 ms) while the host load
  average was 15 to 22. The service refuses to run while `STOPPED.json`
  exists, and nobody reviewed it.
- It was pinned to server image `sha256:096a2e77…`, built in early October, so
  it was not testing what production runs.
- Lab tests are now run on demand against the current images, as in the
  [two services replicas](2026-11-14-two-services-replicas.md) qualification.

The scripts stay in the prefect-server repository under `scripts/chaos/`. They
remain the only way to test Redis and Postgres faults, which cannot be
exercised in production because there is one of each.

## What was removed

- `~/.config/systemd/user/prefect-chaos.service` and `.timer`
- `~/.local/share/prefect-chaos/` (clean checkouts of prefect-server `a20d47b`
  and prefect-mcp-selfhosted `5246edd`, plus a results directory)
- `~/.config/prefect-chaos/environment` (one setting, `CHAOS_SERVER_IMAGE`)
- `~/.local/state/prefect-chaos.lock`

No lab containers, volumes or networks existed at removal.

## What was kept

`~/.local/state/prefect-chaos-validation/` is untouched: every past report,
`history.json`, and the unreviewed `STOPPED.json` from 2026-11-10. A
`retired-2026-11-14/` directory inside it holds copies of the two units, the
environment file, the results directory and the checkout revisions.

## Consumer

`prefect-mcp-selfhosted.service` was started with
`--chaos-state ~/.local/state/prefect-chaos-validation` and served a
`get_chaos_status` tool from that directory. The flag was removed from the
unit the same day (prefect-mcp-selfhosted `3943417`, branch
`codex/selfhosted`) and the service restarted. It now lists 13 tools, none of
them chaos, and nothing else reads the directory.

## Restoring

Copy the two units from `retired-2026-11-14/` back to
`~/.config/systemd/user/`, restore the checkouts and environment file to the
paths above, review and archive `STOPPED.json` as the lab README describes,
then `systemctl --user enable --now prefect-chaos.timer`.

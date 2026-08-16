# prefect patterns

Conventions in `CLAUDE.md` state the rule; this explains the mechanism, with
citations into `~/github.com/prefecthq/prefect` so the next person can check
rather than trust.

## named states of a COMPLETED type

A state's `name` is independent of its `type`. `Cached` and `RolledBack` are
stock COMPLETED-type states carrying their own names
(`docs/v3/concepts/states.mdx`), so a custom name is a sanctioned mechanism and
not a hack.

Returning a manually-constructed `State` from a task or flow makes the run
enter that state **verbatim**, name included — see `return_value_to_state` in
`src/prefect/states.py`, which uses the returned state as-is when it has no
run ids on it (i.e. when the user built it).

Use it when a run genuinely did its job but with something missing:

```python
if degraded:
    return Completed(
        name="Degraded",
        message=f"persisted what we could; unavailable: {', '.join(degraded)}",
    )
```

This beats the two obvious alternatives. A boolean return leaves the run
looking identical to a clean one. Swallowing the error silently loses the fact
that a source was down.

**It also controls alerting.** The state-change event is named
`prefect.flow-run.{state.name}` (`src/prefect/server/models/events.py`), and
our `flow-run failure -> discord` automation expects exactly
`prefect.flow-run.{Failed,TimedOut,Crashed}`. So a `Degraded` run stays visible
and filterable in the UI without paging anyone. To opt a custom state *into*
alerting, add its name to that automation's `expect` list.

## retries on anything that touches the network

House policy, matching what `flows/curate.py` already used:

```python
NETWORK_RETRIES = {
    "retries": 3,
    "retry_delay_seconds": [2, 5, 10],
    "retry_jitter_factor": 1,
}
```

The trap: **do not `try/except` a transient error inside the task.** Prefect
retries on a raised exception, so catching it is what prevents the retry you
wanted. Let it raise, and make the degrade-or-fail decision at the join point,
where you know whether that source is load-bearing for the run.

`flows/ingest.py` shows the shape — `_tolerate()` resolves a future with
`result(raise_on_failure=False)`, which returns the exception object rather
than raising it (`_get_state_result` in `src/prefect/states.py`), substitutes an
empty default, and records the source so the flow can finish `Degraded`.

## why this exists

`ingest` had 16 tasks, 15 of them a bare `@task` with no retries, each joined
with `.result()`. Any single blip discarded the whole run including the sources
that had already succeeded — 7 failures in 337 runs over a month, from three
different call sites. See `docs/incidents/2026-08-16-ingest-single-blip.md`.

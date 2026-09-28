"""hourly safety net for revision requests on gardener pulls.

rung three of the autofix ladder (docs/autofix.md). the fast path is
`mps.pull_comment_bridge`, a service on heavypad subscribed to
stream.waow.tech. this flow is the authority: one listRecords per reviewer
against their PDS, emitting `autofix.revise-requested` for any comment the
bridge has not handled. the event, not this flow, starts autofix-revise.

comments younger than BRIDGE_GRACE belong to the bridge; reconciling them
here would race it into a duplicate revise run. a run that does find
something ends Completed(name="Recovered") — the bridge missed a comment,
which is worth a look even though the comment was delivered.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from mps.pull_comments import (
    created_before,
    load_handled,
    mark_handled,
    reconcile,
    revise_event,
)
from prefect import flow
from prefect.events import emit_event
from prefect.states import Completed, State

BRIDGE_GRACE = timedelta(minutes=10)


@flow(name="watch-tangled-pulls", log_prints=True, timeout_seconds=300)
async def watch_tangled_pulls() -> State:
    handled = await load_handled()
    cutoff = datetime.now(UTC) - BRIDGE_GRACE
    comments = {c["uri"]: c for c in reconcile()}
    missed = [c for c in comments.values() if c["uri"] not in handled and created_before(c, cutoff)]
    print(f"reconciled {len(comments)} comment(s), {len(missed)} missed by the bridge")
    if not missed:
        return Completed()

    for comment in missed:
        emit_event(**revise_event(comment))
        print(f"revise requested for {comment['pull']} by {comment['uri']}")
    await mark_handled([c["uri"] for c in missed])
    return Completed(
        name="Recovered",
        message=f"{len(missed)} comment(s) the bridge missed: "
        + ", ".join(c["uri"] for c in missed),
    )


if __name__ == "__main__":
    asyncio.run(watch_tangled_pulls())

from __future__ import annotations

import httpx
import pytest
from prefect.logging import disable_run_logger

from flows.fastmcp_brief import Brief, BriefItem, render


def _thread(number: int, api_url: str | None = None) -> dict:
    return {
        "thread_id": str(number),
        "number": number,
        "title": f"thread {number}",
        "kind": "PullRequest",
        "url": f"https://github.com/PrefectHQ/fastmcp/pull/{number}",
        "api_url": api_url or f"https://api.github.com/repos/PrefectHQ/fastmcp/pulls/{number}",
        "updated_at": "2026-07-27T18:00:00Z",
        "reason": "subscribed",
    }


def _enrich_against(responses: dict[str, dict], threads: list[dict]) -> list[dict]:
    """Run enrich with a stubbed GitHub, so the filter is tested, not the network."""
    from flows import fastmcp_brief

    def handler(request: httpx.Request) -> httpx.Response:
        body = responses.get(str(request.url))
        if body is None:
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    class StubClient(real_client):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    fastmcp_brief.httpx.Client = StubClient
    try:
        # enrich calls get_run_logger, which needs a run context it will not
        # have here; disable_run_logger is prefect's supported stand-in
        with disable_run_logger():
            return fastmcp_brief.enrich.fn(threads, "token")
    finally:
        fastmcp_brief.httpx.Client = real_client


def test_merged_pull_requests_are_dropped() -> None:
    # the bug: the first delivered brief led with a red "upgrade checks failing
    # on main" for #4657, which had merged four hours earlier. A notification
    # carries no state, and a merge is itself activity — so merging a fix pushes
    # it into the inbox and a title-only brief reports the fix as the outage.
    threads = [_thread(4657), _thread(4653)]
    responses = {
        threads[0]["api_url"]: {"state": "closed", "merged_at": "2026-07-27T14:56:06Z"},
        threads[1]["api_url"]: {"state": "open", "merged_at": None, "user": {"login": "hxaxd"}},
    }
    alive = _enrich_against(responses, threads)
    assert [t["number"] for t in alive] == [4653]


def test_closed_issues_are_dropped_even_without_merged_at() -> None:
    threads = [_thread(4638)]
    responses = {threads[0]["api_url"]: {"state": "closed", "user": {"login": "someone"}}}
    assert _enrich_against(responses, threads) == []


def test_unreachable_thread_is_kept_as_unknown_not_silently_dropped() -> None:
    """A 404 or an outage must not quietly shrink the brief — an item we cannot
    check is still an item, and dropping it would look like a quiet window."""
    threads = [_thread(4999)]
    alive = _enrich_against({}, threads)
    assert len(alive) == 1
    assert alive[0]["state"] == "unknown"


def test_threads_emitted_before_api_url_existed_survive() -> None:
    stale_shape = _thread(4600)
    del stale_shape["api_url"]
    alive = _enrich_against({}, [stale_shape])
    assert len(alive) == 1 and alive[0]["state"] == "unknown"


# --- rendering -------------------------------------------------------------


def _item(number: int, severity: str = "bug") -> BriefItem:
    return BriefItem(
        headline=f"headline {number}",
        why="a reason",
        url=f"https://github.com/PrefectHQ/fastmcp/pull/{number}",
        number=number,
        severity=severity,
    )


def test_render_uses_masked_links_so_discord_does_not_unfurl() -> None:
    # a bare URL becomes a full link-preview card per item, which is what made
    # the first brief unreadable. No bare url may survive rendering.
    body = render(Brief(items=[_item(1), _item(2)], considered=10), 6)
    assert "](https://github.com/" in body
    for line in body.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("http"), line


def test_render_is_empty_when_nothing_survived() -> None:
    assert render(Brief(items=[], considered=40), 6) == ""


def test_render_stays_within_the_discord_budget() -> None:
    from flows.fastmcp_brief import BRIEF_CHAR_BUDGET

    many = Brief(items=[_item(n) for n in range(60)], considered=60)
    body = render(many, 6)
    assert len(body) <= BRIEF_CHAR_BUDGET + 200  # budget + the tail line
    assert "more" in body.splitlines()[-1]


@pytest.mark.parametrize("severity", ["broken", "bug", "waiting", "decision"])
def test_every_severity_has_a_mark(severity: str) -> None:
    body = render(Brief(items=[_item(1, severity)], considered=1), 6)
    assert not body.startswith("⚪"), f"{severity} fell through to the default mark"


# --- links must come from data, never from the model -----------------------


def test_render_prefers_the_thread_url_over_whatever_the_model_emitted() -> None:
    # the model is asked for a url and will happily produce a plausible one.
    # A wrong link reads as a citation, so the link is looked up by number from
    # the threads we actually saw.
    item = _item(4653)
    item.url = "https://github.com/PrefectHQ/fastmcp"  # a homepage link, useless
    threads = [{"number": 4653, "url": "https://github.com/PrefectHQ/fastmcp/pull/4653"}]
    body = render(Brief(items=[item], considered=1), 6, threads)
    assert "/pull/4653)" in body
    assert "fastmcp)" not in body


def test_render_drops_items_whose_number_matches_nothing_we_saw() -> None:
    """A hallucinated number has no trustworthy link, so it cannot be shown."""
    threads = [{"number": 4653, "url": "https://github.com/PrefectHQ/fastmcp/pull/4653"}]
    body = render(Brief(items=[_item(9999)], considered=1), 6, threads)
    assert "9999" not in body


def test_watch_drops_threads_it_cannot_build_a_real_link_for() -> None:
    # this is what produced a bare repo-homepage link in a delivered message
    from flows.watch_fastmcp import _thread_to_event

    unparseable = {
        "id": "1",
        "reason": "subscribed",
        "updated_at": "2026-07-27T18:00:00Z",
        "subject": {"title": "t", "type": "PullRequest", "url": "https://api.github.com/notafile"},
    }
    assert _thread_to_event(unparseable) is None

    # a release subject url ends in a release id, which parses as a number and
    # used to build github.com/<repo>/issues/360744956 — a confident link to an
    # issue that does not exist. Observed 4 times in 200 real notifications.
    release = {
        "id": "2",
        "reason": "subscribed",
        "updated_at": "2026-07-27T18:00:00Z",
        "subject": {
            "title": "v4.0.0",
            "type": "Release",
            "url": "https://api.github.com/repos/PrefectHQ/fastmcp/releases/360744956",
        },
    }
    assert _thread_to_event(release) is None


def test_enrich_takes_githubs_own_html_url() -> None:
    """Links are authoritative, not assembled from a path guess."""
    threads = [_thread(4653)]
    responses = {
        threads[0]["api_url"]: {
            "state": "open",
            "html_url": "https://github.com/PrefectHQ/fastmcp/pull/4653",
            "user": {"login": "hxaxd"},
        }
    }
    alive = _enrich_against(responses, threads)
    assert alive[0]["url"] == "https://github.com/PrefectHQ/fastmcp/pull/4653"

"""Regression tests for curate surviving transient bsky.social 500s.

curate-622f8e09 died when listRecords returned a one-off 500 mid-pagination.
_list_records is now a prefect task with jittered retries, so the engine
re-runs the listing (cheap) instead of one blip killing the run.
"""

import httpx
import pytest
from prefect.cache_policies import NONE

from flows.curate import PHI_DID, _list_records

PAGE_ONE = {"records": [{"uri": "at://x/1"}], "cursor": "c1"}
PAGE_TWO = {"records": [{"uri": "at://x/2"}]}


def test_list_records_has_retries():
    assert _list_records.retries == 3
    assert _list_records.retry_delay_seconds == [2, 5, 10]
    assert _list_records.retry_jitter_factor == 1


def test_list_records_never_caches():
    # the agent deletes records mid-run and re-lists; a same-inputs cache
    # hit would show pre-delete state
    assert _list_records.cache_policy is NONE


def test_list_records_paginates():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "c1":
            return httpx.Response(200, json=PAGE_TWO)
        return httpx.Response(200, json=PAGE_ONE)

    records = _list_records.fn(
        PHI_DID, "network.cosmik.card", transport=httpx.MockTransport(handler)
    )
    assert [r["uri"] for r in records] == ["at://x/1", "at://x/2"]


def test_list_records_raises_on_500_so_the_engine_can_retry():
    transport = httpx.MockTransport(lambda r: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        _list_records.fn(PHI_DID, "network.cosmik.card", transport=transport)

from unittest.mock import MagicMock, patch

import httpx
import pytest
from mps.atproto import resolve_pds

from flows import ingest

DID = "did:plc:xbtmt2zjwlrfegqvch7fboei"
PLC_FETCH = "atproto_identity.did.resolvers.plc_resolver.DidPlcResolver.resolve_without_validation"


def _did_doc(services: list[dict]) -> dict:
    return {
        "@context": ["https://www.w3.org/ns/did/v1"],
        "id": DID,
        "alsoKnownAs": ["at://zzstoatzz.io"],
        "verificationMethod": [],
        "service": services,
    }


@pytest.fixture(autouse=True)
def _fresh_cache():
    resolve_pds.cache_clear()
    yield
    resolve_pds.cache_clear()


def test_resolve_pds_reads_the_atproto_pds_service_once_per_process():
    doc = _did_doc(
        [
            {"id": "#other", "type": "Other", "serviceEndpoint": "https://other.test"},
            {
                "id": "#atproto_pds",
                "type": "AtprotoPersonalDataServer",
                "serviceEndpoint": "https://pds.test",
            },
        ]
    )
    with patch(PLC_FETCH, return_value=doc) as fetch:
        assert resolve_pds(DID) == "https://pds.test"
        assert resolve_pds(DID) == "https://pds.test"
    fetch.assert_called_once_with(DID)


def test_resolve_pds_fails_without_a_pds_service():
    with (
        patch(PLC_FETCH, return_value=_did_doc([])),
        pytest.raises(RuntimeError, match="no #atproto_pds"),
    ):
        resolve_pds(DID)


def _capture_base_url(seen: list[httpx.URL], value):
    def fake(client: httpx.Client, *args, **kwargs):
        seen.append(client.base_url)
        return value

    return fake


def test_likes_ingest_reads_from_the_resolved_pds():
    seen: list[httpx.URL] = []
    with (
        patch.object(ingest, "resolve_pds", return_value="https://pds.test") as resolve,
        patch.object(ingest, "fetch_likes", _capture_base_url(seen, [])),
        patch.object(ingest, "get_run_logger", return_value=MagicMock()),
    ):
        ingest.fetch_nate_likes.fn()
    resolve.assert_called_once_with(ingest.LIKES_DID)
    assert seen == [httpx.URL("https://pds.test")]


def test_tangled_ingest_reads_from_the_resolved_pds():
    seen: list[httpx.URL] = []
    with (
        patch.object(ingest, "resolve_pds", return_value="https://pds.test") as resolve,
        patch.object(ingest, "fetch_repo_at_uris", _capture_base_url(seen, {})),
        patch.object(ingest, "fetch_items", _capture_base_url(seen, [])),
        patch.object(ingest, "get_run_logger", return_value=MagicMock()),
    ):
        ingest.fetch_all_tangled_items.fn()
    resolve.assert_called_once_with(ingest.TANGLED_DID)
    assert set(seen) == {httpx.URL("https://pds.test")}

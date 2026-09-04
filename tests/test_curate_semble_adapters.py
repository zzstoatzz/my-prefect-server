"""Regression tests for curate's Semble API adapter layer."""

from typing import get_args

from semble.records import CARD_TYPE_URL, RECORD_TYPE_CARD
from semble.types import ConnectionType

from flows import curate


def test_url_from_card_uri_uses_semble_record_shape(monkeypatch):
    uri = "at://did:plc:phi/network.cosmik.card/card1"

    def fake_list_records(did: str, collection: str):
        assert collection == RECORD_TYPE_CARD
        return [
            {
                "uri": uri,
                "value": {
                    "type": CARD_TYPE_URL,
                    "content": {"url": "https://example.com/post"},
                },
            }
        ]

    monkeypatch.setattr(curate, "_list_records", fake_list_records)

    assert curate._url_from_card_uri(uri) == "https://example.com/post"


def test_curate_agent_is_janitor_only():
    """curate must never author. authoring from a review of phi's own
    library is how the graph once collapsed into one-topic self-synthesis;
    new cards/collections/connections are created live by the bot only."""
    agent = curate._build_agent("claude-haiku-4-5", "dummy-key")
    tool_names = set(agent._function_toolset.tools)

    forbidden = {
        "add_url_card",
        "create_collection",
        "create_connection",
        "create_note",
    }
    assert not (tool_names & forbidden)
    assert {
        "list_semble_records",
        "delete_record",
        "file_card",
        "update_collection_description",
    } <= tool_names


def test_connection_type_vocabulary_comes_from_semble_sdk():
    connection_types = set(get_args(ConnectionType))

    assert "SUPPLEMENT" in connection_types
    assert "SUPPLEMENTS" not in connection_types


def test_pds_delete_refuses_foreign_repo():
    session = {"did": "did:plc:phi", "accessJwt": "jwt"}
    result = curate._pds_delete_record(session, "at://did:plc:someoneelse/network.cosmik.card/x")
    assert "refusing" in result


def test_pds_delete_posts_delete_record(monkeypatch):
    """orphans the appview can't resolve are deleted straight off the pds."""
    calls = {}

    class FakeResponse:
        def raise_for_status(self):
            return self

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        calls["auth"] = headers.get("Authorization")
        return FakeResponse()

    monkeypatch.setattr(curate.httpx, "post", fake_post)
    session = {"did": "did:plc:phi", "accessJwt": "jwt"}
    uri = "at://did:plc:phi/network.cosmik.collectionLink/orphan1"

    result = curate._pds_delete_record(session, uri)

    assert "deleted" in result
    assert calls["url"].endswith("com.atproto.repo.deleteRecord")
    assert calls["json"] == {
        "repo": "did:plc:phi",
        "collection": "network.cosmik.collectionLink",
        "rkey": "orphan1",
    }
    assert calls["auth"] == "Bearer jwt"


def test_collection_update_carries_required_name():
    """network.cosmik.collection.update requires name even when only the
    description changes — omitting it 400s, which silently blocked every
    trim in the janitor's first run."""
    import inspect

    source = inspect.getsource(curate._build_agent)
    assert "name=collection.name" in source

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

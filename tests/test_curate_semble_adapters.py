"""Regression tests for curate's Semble API adapter layer."""

import asyncio
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


def test_connection_endpoint_value_resolves_url_card_uri(monkeypatch):
    monkeypatch.setattr(
        curate,
        "_url_from_card_uri",
        lambda uri: "https://example.com/resolved" if uri.startswith("at://") else None,
    )

    assert (
        asyncio.run(
            curate._connection_endpoint_value(
                "at://did:plc:phi/network.cosmik.card/card1"
            )
        )
        == "https://example.com/resolved"
    )
    assert (
        asyncio.run(curate._connection_endpoint_value("https://example.com/raw"))
        == "https://example.com/raw"
    )


def test_connection_type_vocabulary_comes_from_semble_sdk():
    connection_types = set(get_args(ConnectionType))

    assert "SUPPLEMENT" in connection_types
    assert "SUPPLEMENTS" not in connection_types

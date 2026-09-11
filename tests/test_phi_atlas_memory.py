"""Atlas extraction must cover later namespaces and rows beyond ANN's cap."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from flows import phi_atlas as atlas


def row(number, **attrs):
    return SimpleNamespace(
        id=f"{number:05}",
        content=f"memory {number}",
        kind="interaction",
        vector=[0.1, 0.2],
        created_at="2026-09-05T00:00:00Z",
        **attrs,
    )


class Pages:
    def __init__(self, names):
        self.namespaces = names[:100]
        self.names = names

    def __iter__(self):
        return iter(self.names)


class Namespace:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["rank_by"] == ("id", "asc")
        assert kwargs["include_attributes"] is True
        lower = kwargs["filters"][2] if kwargs["filters"] else ""
        return SimpleNamespace(rows=[r for r in self.rows if r.id > lower][: kwargs["top_k"]])


def install(monkeypatch, namespaces):
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.namespaces.return_value = Pages(
        [SimpleNamespace(id=name) for name in namespaces if name != atlas.EPISODIC_NS]
    )
    client.namespace.side_effect = namespaces.__getitem__
    monkeypatch.setattr(atlas.turbopuffer, "Turbopuffer", Mock(return_value=client))
    monkeypatch.setattr(atlas, "get_run_logger", Mock(return_value=Mock()))
    return client


def test_all_namespaces_and_all_rows_preserve_identity_vectors_and_sources(monkeypatch):
    namespaces = {f"phi-users-person{i:03}_test": Namespace([row(i)]) for i in range(101)}
    last = namespaces["phi-users-person100_test"] = Namespace(
        [row(i, source_uris=["at://did:plc:alice/app.bsky.feed.post/source"]) for i in range(1001)]
    )
    namespaces[atlas.EPISODIC_NS] = Namespace([row(i) for i in range(1001)])
    install(monkeypatch, namespaces)
    points = atlas.fetch_tpuf_points.fn("test")
    assert len(points) == 2102
    point = next(p for p in points if p.id == "interaction-phi-users-person100_test-01000")
    assert point.refs["source_uris"] == ["at://did:plc:alice/app.bsky.feed.post/source"]
    assert point._vector == [0.1, 0.2]
    assert point.created_at == "2026-09-05T00:00:00Z"
    assert next(p for p in points if p.id == "episodic-01000").refs["source_uris"] == []
    assert len(last.calls) == 3
    assert last.calls[1]["filters"] == ("id", "Gt", "00499")
    assert point.model_dump()["refs"]["source_uris"] == point.refs["source_uris"]
    assert "_vector" not in point.model_dump()


def test_later_page_failure_does_not_publish_partial_success(monkeypatch):
    namespace = Namespace([row(i) for i in range(500)])
    query = namespace.query
    namespace.query = Mock(
        side_effect=[
            query(rank_by=("id", "asc"), include_attributes=True, filters=None, top_k=500),
            RuntimeError("store unavailable"),
        ]
    )
    install(monkeypatch, {"phi-users-alice_test": namespace, atlas.EPISODIC_NS: Namespace([])})
    with pytest.raises(RuntimeError, match="store unavailable"):
        atlas.fetch_tpuf_points.fn("test")
    assert atlas.fetch_tpuf_points.retries == 3


def test_nonadvancing_page_fails_instead_of_looping():
    namespace = Mock()
    namespace.query.return_value = SimpleNamespace(rows=[row(i) for i in range(500)])
    with pytest.raises(RuntimeError, match="did not advance"):
        list(atlas._memory_rows(namespace))


async def test_flow_uses_retrying_task_entrypoint(monkeypatch):
    monkeypatch.setattr(atlas, "configure_logfire", Mock())
    monkeypatch.setattr(atlas, "get_run_logger", Mock(return_value=Mock()))
    monkeypatch.setattr(atlas, "secret", AsyncMock(return_value="test"))
    fetch = Mock(side_effect=RuntimeError("task entrypoint reached"))
    fetch.fn.side_effect = AssertionError("bypassed Prefect retries")
    monkeypatch.setattr(atlas, "fetch_tpuf_points", fetch)
    with pytest.raises(RuntimeError, match="task entrypoint reached"):
        await atlas.phi_atlas.fn(dry_run=True)
    fetch.assert_called_once_with("test")
    fetch.fn.assert_not_called()

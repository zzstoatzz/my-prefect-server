import os
from contextlib import suppress
from uuid import uuid4

import pytest
import turbopuffer

from flows.compact import query_active_observations


@pytest.fixture
def namespace():
    key = os.environ.get("TURBOPUFFER_TEST_API_KEY")
    if not key:
        pytest.skip("requires TURBOPUFFER_TEST_API_KEY for disposable live namespaces")
    with turbopuffer.Turbopuffer(api_key=key, region="gcp-us-central1") as client:
        ns = client.namespace(f"mps-compact-test-{uuid4().hex}")
        try:
            yield ns
        finally:
            with suppress(turbopuffer.NotFoundError):
                ns.delete_all()


@pytest.mark.parametrize("rank_by", [("created_at", "desc"), ("vector", "ANN", [1.0, 0.0])])
def test_observation_reads_across_schema_generations(namespace, rank_by):
    def read():
        return query_active_observations(namespace, rank_by=rank_by, top_k=10)

    assert read() == []
    legacy = {
        "id": "conversation",
        "type": "conversation",
        "content": "an old exchange",
        "created_at": "2026-01-01",
        "vector": [1.0, 0.0],
    }
    namespace.write(upsert_rows=[legacy], distance_metric="cosine_distance")
    assert read() == []
    namespace.write(
        upsert_rows=[
            {
                "id": "old-observation",
                "kind": "observation",
                "content": "a fact without status or tags",
                "created_at": "2026-01-02",
                "vector": [1.0, 0.0],
            }
        ]
    )
    assert {row.id for row in read()} == {"old-observation"}
    namespace.write(
        upsert_rows=[
            {
                "id": row_id,
                "kind": kind,
                "status": status,
                "created_at": "2026-01-03",
                "vector": [1.0, 0.0],
            }
            for row_id, kind, status in [
                ("active", "observation", "active"),
                ("superseded", "observation", "superseded"),
                ("summary", "summary", "active"),
                ("interaction", "interaction", "active"),
            ]
        ]
    )
    assert {row.id for row in read()} == {"old-observation", "active"}
    stored = namespace.query(rank_by=("id", "asc"), top_k=100, include_attributes=True)
    conversation = next(row for row in stored.rows if row.id == "conversation")
    assert conversation.content == legacy["content"]
    assert getattr(conversation, "kind", None) is None
    assert len(stored.rows) == 6


def test_invalid_queries_are_not_reported_as_empty_memory(namespace):
    namespace.write(
        upsert_rows=[{"id": "observation", "kind": "observation", "vector": [1.0, 0.0]}],
        distance_metric="cosine_distance",
    )
    with pytest.raises(turbopuffer.BadRequestError):
        query_active_observations(namespace, rank_by=("missing", "desc"), top_k=10)

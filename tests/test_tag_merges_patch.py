"""Tag maintenance must not rewrite rows it only means to retag.

2026-10-07: apply_tag_merges upserted each retagged row with only content,
tags, created_at and kind. An upsert replaces the row, so status, supersedes
and source_uris were dropped, and 96 of the 363 observations superseded by
the 10-03 duplicate merge were active again four days later.
"""

from types import SimpleNamespace

import flows.morning as morning
from flows.morning import apply_tag_merges, canonical_tags


def test_aliases_collapse_to_the_canonical_tag_once():
    assert canonical_tags(["llms", "rust", "llm"], {"llms": "llm"}) == ["llm", "rust"]


def test_retagging_patches_tags_and_nothing_else(monkeypatch):
    writes = []

    class Namespace:
        def query(self, **_):
            return SimpleNamespace(
                rows=[
                    SimpleNamespace(id="stale", tags=["llms"], status="superseded"),
                    SimpleNamespace(id="untouched", tags=["rust"]),
                ]
            )

        def write(self, **kwargs):
            writes.append(kwargs)

    client = SimpleNamespace(
        namespaces=lambda prefix: SimpleNamespace(namespaces=[]),
        namespace=lambda name: Namespace(),
    )
    monkeypatch.setattr(morning.turbopuffer, "Turbopuffer", lambda **_: client)

    updated = apply_tag_merges.fn("key", [{"canonical": "llm", "aliases": ["llms"]}])

    assert updated == 1
    assert writes == [{"patch_rows": [{"id": "stale", "tags": ["llm"]}]}]

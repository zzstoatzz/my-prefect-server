"""Checkpoint rollback selection against real HTTP and retained disk objects."""

import json

import pytest
from mps.typeahead_checkpoint_release import read_anchor
from mps.typeahead_refresh import ROUTE_KEY

from flows.typeahead_index import typeahead_index
from tests.test_typeahead_engine_compatibility import (
    NATIVE,
    TANTIVY,
    controller as controller,
)


@pytest.mark.parametrize("current", [NATIVE, TANTIVY])
def test_native_anchor_read_preserves_route_and_payloads(controller, current):
    route = {"version": 1, "current": current, "native_rollback": NATIVE}
    original = json.dumps(route).encode()
    controller.s3.seed(ROUTE_KEY, original)
    native = {"active_build": NATIVE["build_id"], "source_watermark": NATIVE["source_watermark"]}
    raw = json.dumps(native).encode() + b"\n"
    controller.s3.seed(f"releases/search/{NATIVE['build_id']}/manifest.json", raw)
    assert read_anchor(controller) == raw
    assert controller.s3.path(ROUTE_KEY).read_bytes() == original
    assert controller.s3.writes == controller.s3.deletions == []


def test_native_anchor_mismatched_watermark_is_rejected(controller):
    controller.s3.seed(ROUTE_KEY, json.dumps({"version": 1, "current": NATIVE}).encode())
    native = {
        "active_build": NATIVE["build_id"],
        "source_watermark": NATIVE["source_watermark"] - 1,
    }
    controller.s3.seed(
        f"releases/search/{NATIVE['build_id']}/manifest.json", json.dumps(native).encode()
    )
    with pytest.raises(ValueError, match="differs"):
        read_anchor(controller)
    assert controller.s3.writes == controller.s3.deletions == []


@pytest.mark.parametrize(
    "options", [{"reuse_source": "/retired/source"}, {"resume_generation": "/retired/generation"}]
)
def test_retired_tantivy_preparation_cannot_start_another_source_build(options):
    with pytest.raises(ValueError, match="one new owned cloud checkpoint"):
        typeahead_index.fn(snapshot_engine="tantivy", snapshot_format="compact", **options)

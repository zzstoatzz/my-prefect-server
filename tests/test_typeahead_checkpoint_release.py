"""Checkpoint builds carry the serving artifact's native provenance anchor from R2."""

import hashlib

import pytest
from mps.typeahead_checkpoint_release import read_anchor
from mps.typeahead_refresh import ROUTE_KEY

from tests.test_typeahead_engine_compatibility import (
    NATIVE,
    TANTIVY,
    controller as controller,
    pinned_adapter as pinned_adapter,
    seed_checkpoint_publication,
    seed_stub_publication,
)


def test_anchor_comes_from_the_serving_artifact_without_a_native_backend(controller):
    _, _, anchor = seed_stub_publication(controller, TANTIVY)
    route = controller.s3.path(ROUTE_KEY).read_bytes()
    assert read_anchor(controller) == anchor
    assert controller.s3.path(ROUTE_KEY).read_bytes() == route
    assert controller.s3.writes == controller.s3.deletions == []


def test_changed_anchor_bytes_or_missing_publication_are_rejected(controller):
    _, _, anchor = seed_stub_publication(controller, TANTIVY)
    key = f"releases/search/{NATIVE['build_id']}/manifest.json"
    controller.s3.seed(key, anchor + b" ")
    with pytest.raises(ValueError, match="manifest bytes differ"):
        read_anchor(controller)
    controller.s3.path(f"releases/search-tantivy/{TANTIVY['build_id']}/manifest.json").unlink()
    with pytest.raises(ValueError, match="publication is missing"):
        read_anchor(controller)
    assert controller.s3.writes == controller.s3.deletions == []


def test_pinned_installer_accepts_the_carried_anchor(controller, pinned_adapter):
    controller.tantivy_adapter = lambda: pinned_adapter
    manifest, native_raw = seed_checkpoint_publication(controller, TANTIVY)
    assert read_anchor(controller) == native_raw
    assert manifest["rollback_native_anchor"]["manifest_sha256"] == (
        hashlib.sha256(native_raw).hexdigest()
    )

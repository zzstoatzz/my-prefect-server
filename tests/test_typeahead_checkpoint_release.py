"""Checkpoint builds carry the serving artifact's native provenance anchor from R2."""

import hashlib
import json

import pytest
from mps.typeahead_checkpoint_release import read_anchor
from mps.typeahead_refresh import ROUTE_KEY

from tests.test_typeahead_engine_compatibility import (
    NATIVE,
    TANTIVY,
    controller as controller,
    pinned_adapter as pinned_adapter,
    seed_checkpoint_publication,
)


class AnchorAdapter:
    """Binds the anchor bytes by checksum, as the pinned installer does."""

    def validate_manifest(self, manifest):
        return manifest

    def validate_source(self, manifest, raw):
        if hashlib.sha256(raw).hexdigest() != manifest["rollback_native_anchor"]["manifest_sha256"]:
            raise ValueError("Exact rollback-native manifest bytes differ")


def no_native_reads(controller):
    original = controller.get

    def get(base, path):
        assert base != NATIVE["origin"], "the anchor must come from R2, not a native backend"
        return original(base, path)

    controller.get = get


def seed_anchor(controller):
    anchor = json.dumps({"active_build": NATIVE["build_id"], "manifest_version": 2}).encode()
    manifest = {
        "engine": "tantivy",
        "active_build": TANTIVY["build_id"],
        "backend_format": TANTIVY["format"],
        "binary_sha256": TANTIVY["binary_sha256"],
        "retrieval_strategy": TANTIVY["retrieval_strategy"],
        "source_kind": "sqlite-committed-prefix",
        "rollback_native_anchor": {
            "build_id": NATIVE["build_id"],
            "manifest_sha256": hashlib.sha256(anchor).hexdigest(),
        },
    }
    controller.s3.seed(f"releases/search/{NATIVE['build_id']}/manifest.json", anchor)
    controller.s3.seed(
        f"releases/search-tantivy/{TANTIVY['build_id']}/manifest.json",
        json.dumps(manifest).encode(),
    )
    controller.tantivy_adapter = AnchorAdapter
    return anchor


def test_anchor_comes_from_the_serving_artifact_without_a_native_backend(controller):
    no_native_reads(controller)
    anchor = seed_anchor(controller)
    route = controller.s3.path(ROUTE_KEY).read_bytes()
    assert read_anchor(controller) == anchor
    assert controller.s3.path(ROUTE_KEY).read_bytes() == route
    assert controller.s3.writes == controller.s3.deletions == []


def test_changed_anchor_bytes_or_missing_publication_are_rejected(controller):
    no_native_reads(controller)
    anchor = seed_anchor(controller)
    key = f"releases/search/{NATIVE['build_id']}/manifest.json"
    controller.s3.seed(key, anchor + b" ")
    with pytest.raises(ValueError, match="manifest bytes differ"):
        read_anchor(controller)
    controller.s3.path(f"releases/search-tantivy/{TANTIVY['build_id']}/manifest.json").unlink()
    with pytest.raises(ValueError, match="publication is missing"):
        read_anchor(controller)
    assert controller.s3.writes == controller.s3.deletions == []


def test_pinned_installer_accepts_the_carried_anchor(controller, pinned_adapter):
    no_native_reads(controller)
    controller.tantivy_adapter = lambda: pinned_adapter
    manifest, native_raw = seed_checkpoint_publication(controller, TANTIVY)
    assert read_anchor(controller) == native_raw
    assert manifest["rollback_native_anchor"]["manifest_sha256"] == (
        hashlib.sha256(native_raw).hexdigest()
    )

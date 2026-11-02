"""Post-publication retirement of owned Tantivy publication workspaces."""

import hashlib
import json

import pytest

import flows.typeahead_index as indexer


@pytest.fixture
def publication(tmp_path, monkeypatch):
    monkeypatch.setattr(indexer, "INDEXER_HOME", tmp_path)
    workspace = tmp_path / "tantivy-builds/build-b1793300000-fixture"
    generation = workspace / "publication"
    generation.mkdir(parents=True)
    (workspace / "owner").write_text("typeahead-index-tantivy-v1\n")
    (workspace / "data").mkdir()
    (workspace / "data/segment").write_bytes(b"retire after verified publication")
    (generation / "index.tar").write_bytes(b"owned published bundle")
    (generation / "manifest.json").write_text('{"active_build":"b1793300000-fixture"}')
    checksum = hashlib.sha256((generation / "manifest.json").read_bytes()).hexdigest()
    (workspace / "published.json").write_text(
        json.dumps({"remote_readback_passed": True, "manifest_sha256": checksum})
    )
    return workspace, generation


def test_postpublication_retirement_is_separate_idempotent_and_keeps_receipts(publication):
    workspace, generation = publication
    assert indexer.retire_tantivy_generation.fn(generation) == generation
    assert not (workspace / "data").exists()
    assert not (generation / "index.tar").exists()
    assert (generation / "manifest.json").is_file()
    assert (workspace / "published.json").is_file()
    assert (workspace / "retired.json").is_file()
    assert indexer.retire_tantivy_generation.fn(generation) == generation


def test_unverified_or_symlinked_retirement_preserves_owned_payloads(publication, tmp_path):
    workspace, generation = publication
    original = (workspace / "published.json").read_bytes()
    (workspace / "published.json").write_text('{"remote_readback_passed":false}')
    with pytest.raises(ValueError, match="publication receipt"):
        indexer.retire_tantivy_generation.fn(generation)
    assert (workspace / "data/segment").is_file()
    (workspace / "published.json").write_bytes(original)
    outside = tmp_path / "outside.tar"
    outside.write_bytes(b"preserve outside")
    (generation / "index.tar").unlink()
    (generation / "index.tar").symlink_to(outside)
    with pytest.raises(ValueError, match="symlinks"):
        indexer.retire_tantivy_generation.fn(generation)
    assert outside.read_bytes() == b"preserve outside"
    assert (workspace / "data/segment").is_file()

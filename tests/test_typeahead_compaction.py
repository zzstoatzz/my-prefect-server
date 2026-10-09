import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from flows.typeahead_index import builds_to_prune, compact_target, publish_compact


def test_compact_target_fails_closed(monkeypatch):
    monkeypatch.setenv("INDEX_CHANNEL", "prod")
    monkeypatch.delenv("INDEX_ALLOW_PROD", raising=False)
    with pytest.raises(ValueError, match="not armed"):
        compact_target()
    monkeypatch.setenv("INDEX_ALLOW_PROD", "1")
    assert compact_target() == ("builds", "latest.json")
    monkeypatch.setenv("INDEX_CHANNEL", "staging")
    monkeypatch.setenv("INDEX_STAGING_NAME", "../prod")
    with pytest.raises(ValueError, match="safe"):
        compact_target()
    monkeypatch.setenv("INDEX_STAGING_NAME", "compact-test")
    assert compact_target() == ("staging/compact-test", "staging/compact-test/latest.json")
    monkeypatch.setenv("INDEX_CHANNEL", "unknown")
    with pytest.raises(ValueError, match="unknown"):
        compact_target()


def test_cleanup_retains_serving_legacy_twin():
    paths = [
        Path("build-" + name)
        for name in ("b100", "b100-px05", "b200", "b200-px05", "b300", "b300-px05")
    ]
    victims = builds_to_prune(paths, "b100-px05")
    assert {p.name for p in victims} == {"build-b200", "build-b200-px05"}


def test_real_converter_flow_retry(tmp_path, monkeypatch):
    configured = os.environ.get("TYPEAHEAD_COMPACTION_SOURCE")
    if configured is None:
        pytest.skip("set TYPEAHEAD_COMPACTION_SOURCE to the typeahead release checkout")
    repo = Path(configured)
    source_root = tmp_path / "source"
    subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/snapshot_compaction/recovery_fixture.py"),
            str(source_root),
        ],
        check=True,
    )
    source = source_root / "builds/legacy"
    monkeypatch.setenv("INDEX_CHANNEL", "local")
    before = (source / "index.db").read_bytes()
    output = publish_compact.fn(repo, source)
    original = (output / "index.db.prefix").read_bytes()
    assert publish_compact.fn(repo, source) == output
    assert (output / "index.db.prefix").read_bytes() == original
    assert (source / "index.db").read_bytes() == before
    manifest = json.loads((output / "manifest.json").read_text())
    manifest["source_watermark"] = 99
    (output / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="does not match"):
        publish_compact.fn(repo, source)

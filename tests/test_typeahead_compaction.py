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
        for name in (
            "b100",
            "b100-px05",
            "b200",
            "b200-px05",
            "b300",
            "b300-px05",
            "b400",
            "b400-px05",
        )
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
    partial = source.parent / "build-legacy-px05"
    partial.mkdir()
    (partial / "interrupted-copy").write_bytes(b"incomplete conversion")
    before = (source / "index.db").read_bytes()
    output = publish_compact.fn(repo, source)
    assert not (output / "interrupted-copy").exists()
    original = (output / "index.db.prefix").read_bytes()
    assert publish_compact.fn(repo, source) == output
    assert (output / "index.db.prefix").read_bytes() == original
    assert (source / "index.db").read_bytes() == before
    manifest = json.loads((output / "manifest.json").read_text())
    manifest["source_watermark"] = 99
    (output / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="does not match"):
        publish_compact.fn(repo, source)


def test_completed_generation_recovery_rejects_other_roots_and_stale_data(tmp_path, monkeypatch):
    from flows.typeahead_index import publish_completed_generation

    active, archive = tmp_path / "active", tmp_path / "archive"
    active.mkdir()
    archive.mkdir()
    monkeypatch.setenv("INDEX_BUILD_ROOT", str(active))
    monkeypatch.setenv("INDEX_ARCHIVE_ROOT", str(archive))
    monkeypatch.setenv("INDEX_CHANNEL", "managed")
    assert compact_target() == ("releases/search", "control/builds/latest.json")
    with pytest.raises(ValueError, match="belong to this publisher"):
        publish_completed_generation.fn(tmp_path, str(tmp_path / "other"))
    generation = active / "build-b1792908655-test-px05"
    generation.mkdir()
    (generation / "manifest.json").write_text(
        json.dumps({"manifest_version": 2, "source_watermark": 100})
    )
    with pytest.raises(ValueError, match="fresh compact snapshot"):
        publish_completed_generation.fn(tmp_path, str(generation))


@pytest.mark.parametrize("update_pointer", [True, False])
def test_completed_publication_with_real_rclone_links_and_retry(
    tmp_path, monkeypatch, update_pointer
):
    import hashlib
    import shutil
    import sqlite3
    import time

    import flows.typeahead_index as flow

    configured = os.environ.get("TYPEAHEAD_COMPACTION_SOURCE")
    if configured is None or shutil.which("rclone") is None:
        pytest.skip("requires the release checkout and real rclone")
    repo = Path(configured)
    active, archive, work, remote = [tmp_path / n for n in ["active", "archive", "work", "remote"]]
    active.mkdir()
    archive.mkdir()
    fixture = tmp_path / "fixture"
    subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/snapshot_compaction/recovery_fixture.py"),
            str(fixture),
        ],
        check=True,
    )
    bid = f"b{int(time.time())}-links"
    source = active / ("build-" + bid)
    shutil.move(fixture / "builds/legacy", source)
    watermark = int(time.time())
    with sqlite3.connect(source / "index.db") as db:
        db.execute("UPDATE index_meta SET value=? WHERE key='build_id'", (bid,))
        db.execute("UPDATE index_meta SET value=? WHERE key='source_watermark'", (str(watermark),))
    manifest = json.loads((source / "manifest.json").read_text())
    manifest.update(
        active_build=bid, source_watermark=watermark, snapshot_url=f"builds/{bid}/index.db"
    )
    with (source / "index.db").open("rb") as stream:
        manifest["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    (source / "manifest.json").write_text(json.dumps(manifest))
    compact = active / ("build-" + bid + "-px05")
    subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/snapshot_compaction/convert.py"),
            "--unlimited-cpu",
            "--source",
            str(source / "index.db"),
            "--manifest",
            str(source / "manifest.json"),
            "--output",
            str(compact),
            "--build-id",
            bid + "-px05",
        ],
        check=True,
    )
    monkeypatch.setattr(flow, "INDEXER_HOME", work)
    for name, value in {
        "INDEX_BUILD_ROOT": str(active),
        "INDEX_ARCHIVE_ROOT": str(archive),
        "INDEX_CHANNEL": "managed",
        "INDEX_R2_BUCKET": str(remote),
        "INDEX_R2_ENDPOINT": "https://unused.invalid",
        "INDEX_R2_ACCESS_KEY_ID": "unused",
        "INDEX_R2_SECRET_ACCESS_KEY": "unused",
        "RCLONE_CONFIG_R2_TYPE": "local",
    }.items():
        monkeypatch.setenv(name, value)
    pointer = remote / "control/builds/latest.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_bytes(b'{"unrelated":"existing live source"}')
    original_pointer = pointer.read_bytes()
    output = flow.publish_completed_generation.fn(repo, str(compact), update_pointer=update_pointer)
    before = pointer.read_bytes()
    published = json.loads(
        (remote / "releases/search" / (bid + "-px05") / "manifest.json").read_bytes()
    )
    if not update_pointer:
        assert before == original_pointer
    assert published["active_build"] == bid + "-px05"
    assert (remote / published["snapshot_url"]).read_bytes() == (compact / "index.db").read_bytes()
    link = output / "index.db"
    link.unlink()
    os.link(compact / "index.db", link)
    assert (
        flow.publish_completed_generation.fn(repo, str(compact), update_pointer=update_pointer)
        == output
    )
    assert pointer.read_bytes() == before
    assert (remote / published["prefix_map"]["object_key"]).read_bytes() == (
        compact / "index.db.prefix"
    ).read_bytes()


@pytest.mark.skipif(sys.platform != "linux", reason="requires real Linux process file references")
def test_staging_cleanup_preserves_active_recent_and_completed_data(tmp_path):
    from flows.typeahead_index import cleanup_abandoned_compactions

    paths = [
        tmp_path / name
        for name in [
            ".compact-release-abandoned",
            ".compact-release-open",
            ".compact-release-recent",
            "build-completed",
        ]
    ]
    for path in paths:
        path.mkdir()
        (path / "data").write_bytes(b"actual staging data")
    for path in paths[:2]:
        os.utime(path / "data", (100, 100))
        os.utime(path, (100, 100))
    (tmp_path / ".compact-release-symlink").symlink_to(paths[3], target_is_directory=True)
    with (paths[1] / "data").open("rb"):
        result = cleanup_abandoned_compactions(tmp_path)
        assert not paths[0].exists() and paths[1].exists()
        assert str(paths[1]) in result["busy"]
    cleanup_abandoned_compactions(tmp_path)
    assert not paths[1].exists()
    assert paths[2].exists() and paths[3].exists()
    assert (tmp_path / ".compact-release-symlink").is_symlink()

import hashlib
import json
import shutil
import sqlite3

import pytest

from flows.typeahead_index import archive_completed_generations, require_build_space


def generation(root):
    path = root / "build-b100-test"
    path.mkdir(parents=True)
    with sqlite3.connect(path / "index.db") as db:
        db.execute("CREATE TABLE actor(handle TEXT)")
        db.execute("INSERT INTO actor VALUES('example.test')")
    data = (path / "index.db").read_bytes()
    (path / "manifest.json").write_text(
        json.dumps({"byte_size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    )
    return path


def test_archive_preserves_sqlite_and_is_idempotent(tmp_path):
    root, archive = tmp_path / "active", tmp_path / "archive"
    source = generation(root)
    result = archive_completed_generations(root, archive)
    assert not source.exists()
    with sqlite3.connect(result[0] / "index.db") as db:
        assert db.execute("SELECT handle FROM actor").fetchone() == ("example.test",)
    shutil.copytree(result[0], source)
    assert archive_completed_generations(root, archive) == result
    assert archive_completed_generations(root, archive) == []


def test_changed_archive_does_not_remove_source(tmp_path):
    root, archive = tmp_path / "active", tmp_path / "archive"
    source = generation(root)
    destination = archive / source.name
    shutil.copytree(source, destination)
    with sqlite3.connect(destination / "index.db") as db:
        db.execute("UPDATE actor SET handle='changed.test'")
    with pytest.raises(ValueError, match="checksum"):
        archive_completed_generations(root, archive)
    assert source.exists()


def test_incomplete_generation_stays_on_active_disk(tmp_path):
    root, archive = tmp_path / "active", tmp_path / "archive"
    source = root / "build-b100-incomplete"
    source.mkdir(parents=True)
    assert archive_completed_generations(root, archive) == []
    assert source.exists()


def test_archive_cannot_overlap_active_root(tmp_path):
    source = generation(tmp_path)
    for archive in [tmp_path, source / "archive", tmp_path.parent]:
        with pytest.raises(ValueError, match="overlap"):
            archive_completed_generations(tmp_path, archive)
    assert source.exists()


def test_build_space_guard_uses_real_disk_capacity(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_BUILD_ROOT", str(tmp_path))
    monkeypatch.setenv("INDEX_MIN_FREE_GIB", "0")
    require_build_space()
    monkeypatch.setenv("INDEX_MIN_FREE_GIB", "1000000000")
    with pytest.raises(RuntimeError, match="free space"):
        require_build_space()


def test_corrupt_compact_prefix_does_not_remove_source(tmp_path):
    root, archive = tmp_path / "active", tmp_path / "archive"
    source = generation(root)
    prefix = b"a complete prefix payload"
    (source / "index.db.prefix").write_bytes(prefix)
    manifest = json.loads((source / "manifest.json").read_text())
    manifest["prefix_map"] = {
        "byte_size": len(prefix),
        "sha256": hashlib.sha256(prefix).hexdigest(),
    }
    (source / "manifest.json").write_text(json.dumps(manifest))
    destination = archive / source.name
    shutil.copytree(source, destination)
    (destination / "index.db.prefix").write_bytes(b"x" * len(prefix))
    with pytest.raises(ValueError, match=r"index\.db\.prefix"):
        archive_completed_generations(root, archive)
    assert source.exists()


def test_stream_reserve_refuses_to_start_child(tmp_path):
    import os
    import sys

    from flows.typeahead_index import _stream

    marker = tmp_path / "started"
    env = dict(
        os.environ,
        INDEX_BUILD_ROOT=str(tmp_path),
        INDEX_MIN_REMAIN_GIB="1000000000",
    )
    with pytest.raises(RuntimeError, match="disk reserve"):
        _stream(
            [sys.executable, "-c", f"open({str(marker)!r},'w').write('started')"],
            tmp_path,
            env,
            timeout=10,
        )
    assert not marker.exists()


def test_stream_stops_real_child_if_watched_disk_disappears(tmp_path):
    import os
    import sys

    from flows.typeahead_index import _stream

    root = tmp_path / "watched"
    root.mkdir()
    if shutil.disk_usage(root).free < 2**30:
        pytest.skip("requires one GiB of real free space")
    pid_file = tmp_path / "pid"
    code = (
        "import os,time,pathlib; "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        f"pathlib.Path({str(root)!r}).rmdir(); "
        "print('disk disappeared',flush=True); time.sleep(30)"
    )
    env = dict(os.environ, INDEX_BUILD_ROOT=str(root), INDEX_MIN_REMAIN_GIB="1")
    with pytest.raises(FileNotFoundError):
        _stream([sys.executable, "-c", code], tmp_path, env, timeout=10)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)

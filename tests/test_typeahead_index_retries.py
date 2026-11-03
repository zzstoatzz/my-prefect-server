import os
import sys
import time
from pathlib import Path

import pytest

from flows.typeahead_index import ProcessExecutionError, _stream


def test_stream_preserves_failure_output(tmp_path):
    with pytest.raises(ProcessExecutionError) as raised:
        _stream(
            [
                sys.executable,
                "-c",
                "import sys; print('error(turso): HttpChunkTruncated', file=sys.stderr); sys.exit(1)",
            ],
            tmp_path,
            dict(os.environ),
            timeout=10,
        )
    assert raised.value.returncode == 1
    assert "HttpChunkTruncated" in str(raised.value)


@pytest.mark.parametrize(
    "behavior",
    [
        "time.sleep(30)",
        "print('partial', end='', flush=True); time.sleep(30)",
        "os.close(1); os.close(2); time.sleep(30)",
    ],
)
def test_stream_deadline_includes_output_read(tmp_path, behavior):
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="exceeded"):
        _stream(
            [sys.executable, "-c", "import os, time; " + behavior],
            tmp_path,
            dict(os.environ),
            timeout=0.2,
        )
    assert time.monotonic() - started < 3


def test_stream_timeout_kills_descendants(tmp_path):
    marker = tmp_path / "survived"
    child = f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).touch()"
    parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
    with pytest.raises(RuntimeError, match="exceeded"):
        _stream([sys.executable, "-c", parent], tmp_path, dict(os.environ), timeout=0.3)
    time.sleep(1)
    assert not marker.exists()


def test_flow_retry_resumes_after_finished_checkpoint_stages(tmp_path, monkeypatch):
    import mps.typeahead_checkpoint_release as release
    from prefect import flow
    from prefect.filesystems import LocalFileSystem
    from prefect.testing.utilities import prefect_test_harness

    import flows.typeahead_index as publisher

    calls = []
    monkeypatch.setattr(
        release, "export_cloud_checkpoint", lambda repo: calls.append("export") or {"root": "r"}
    )
    monkeypatch.setattr(
        release,
        "import_cloud_checkpoint",
        lambda repo, exported: calls.append("import") or {"build": "b"},
    )
    attempts = []

    def freeze(repo, exported, built):
        calls.append("freeze")
        if not attempts:
            attempts.append(1)
            raise RuntimeError("freeze interrupted")
        return Path("publication"), Path("native-anchor")

    monkeypatch.setattr(release, "freeze_cloud_checkpoint", freeze)

    with prefect_test_harness():
        storage = LocalFileSystem(basepath=str(tmp_path / "results"))
        storage.save("stage-results")
        for name in ("export_checkpoint", "import_checkpoint", "freeze_checkpoint"):
            stage = getattr(publisher, name)
            monkeypatch.setattr(
                publisher,
                name,
                stage.with_options(result_storage="local-file-system/stage-results", retries=0),
            )

        @flow(retries=1, retry_delay_seconds=0)
        def release_once():
            return publisher.prepare_checkpoint_generation(tmp_path)

        assert release_once() == (Path("publication"), Path("native-anchor"))
    assert calls == ["export", "import", "freeze", "freeze"]

import os
import sys
import time

import pytest
from prefect import flow
from prefect.testing.utilities import prefect_test_harness

from flows.typeahead_index import ProcessExecutionError, _stream, run_indexer


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


@pytest.fixture(scope="module")
def prefect_server():
    with prefect_test_harness():
        yield


@pytest.mark.parametrize("failures,attempts", [(1, 2), (4, 4)])
def test_indexer_retries_real_process(tmp_path, monkeypatch, prefect_server, failures, attempts):
    binary = tmp_path / "indexer"
    binary.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        "import sys\n"
        "counter = Path('attempts')\n"
        "attempt = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "counter.write_text(str(attempt))\n"
        f"if attempt <= {failures}:\n"
        "    print('error(turso): /export http failed: HttpChunkTruncated', file=sys.stderr)\n"
        "    sys.exit(1)\n"
    )
    binary.chmod(0o755)
    (tmp_path / "rclone").symlink_to(sys.executable)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("INDEX_BUILD_ROOT", str(tmp_path / "build"))

    @flow
    def trial():
        run_indexer.with_options(retry_delay_seconds=0)(binary)

    if failures == 4:
        with pytest.raises(ProcessExecutionError, match="HttpChunkTruncated"):
            trial()
    else:
        trial()
    assert (tmp_path / "attempts").read_text() == str(attempts)


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

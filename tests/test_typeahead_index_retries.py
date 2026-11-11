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

    import flows.typeahead_index as publisher
    from tests.harness import prefect_test_harness

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
        return Path("publication")

    monkeypatch.setattr(release, "freeze_cloud_checkpoint", freeze)

    monkeypatch.setattr(
        publisher, "freeze_checkpoint", publisher.freeze_checkpoint.with_options(retries=0)
    )

    with prefect_test_harness():
        storage = LocalFileSystem(basepath=str(tmp_path / "results"))
        storage.save("stage-results")

        @flow(retries=1, retry_delay_seconds=0)
        def release_once():
            return publisher.prepare_checkpoint_generation(tmp_path, storage)

        assert release_once() == Path("publication")
    assert calls == ["export", "import", "freeze", "freeze"]


def test_stage_tasks_take_their_result_storage_as_a_block_instance():
    import flows.typeahead_index as publisher

    for stage in (
        publisher.export_checkpoint,
        publisher.import_checkpoint,
        publisher.freeze_checkpoint,
        publisher.publish_tantivy_generation,
    ):
        assert stage.persist_result is True
        assert not isinstance(stage.result_storage, str), stage.name


def test_full_refresh_success_survives_retry_without_counting_another_runs_recovery(tmp_path):
    import json
    from uuid import UUID

    from prefect import flow
    from prefect.client.orchestration import get_client
    from prefect.runtime import flow_run

    from flows.typeahead_index import automatic_completion
    from tests.harness import prefect_test_harness

    executions = []

    @flow(retries=1, retry_delay_seconds=0)
    def refresh_once(marker: Path, fail_after_publication: bool):
        executions.append(UUID(str(flow_run.id)))
        if not marker.exists():
            marker.write_text(json.dumps({"publication_execution_id": flow_run.id}))
            if fail_after_publication:
                raise RuntimeError("Interrupted after durable publication")
        return automatic_completion(json.loads(marker.read_text()), str(flow_run.id))

    with prefect_test_harness():
        for retry in [False, True]:
            marker = tmp_path / f"publication-{retry}.json"
            refresh_once(marker, retry)
            with get_client(sync_client=True) as client:
                run = client.read_flow_run(executions[-1])
                assert run.run_count == (2 if retry else 1)
                assert run.state is not None
                assert run.state.name == "Completed"
                assert json.loads(marker.read_text())["publication_execution_id"] == str(run.id)
            refresh_once(marker, False)
            with get_client(sync_client=True) as client:
                run = client.read_flow_run(executions[-1])
                assert run.state is not None and run.state.is_completed()
                assert run.state.name == "RolloutResumed"
        legacy = tmp_path / "legacy.json"
        legacy.write_text("{}")
        refresh_once(legacy, False)
        with get_client(sync_client=True) as client:
            run = client.read_flow_run(executions[-1])
            assert run.state is not None and run.state.name == "RolloutResumed"

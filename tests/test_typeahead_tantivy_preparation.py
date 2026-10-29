"""Offline production-stage exercise with existing native fixtures and real Rust."""

import hashlib
import importlib.util
import json
import os
import platform
import subprocess
from pathlib import Path

import pytest
from mps.typeahead_tantivy import import_snapshot, reconcile_checkpoint

import flows.typeahead_index as indexer
from flows.typeahead_index import typeahead_index

LINUX_EXERCISE = r"""
import importlib.util,json,os,sys,tempfile
from pathlib import Path
sys.path.insert(0,'/app')
import full_fixture
spec=importlib.util.spec_from_file_location('actual_mps_adapter','/mps_adapter.py')
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
with tempfile.TemporaryDirectory(prefix='mps-preparation-') as tmp:
    root=Path(tmp);built=full_fixture.build(root/'fixture',app='/app')
    source=built['source'];(source/'manifest.json').write_text(json.dumps(built['manifest']))
    workspace=root/'builder';workspace.mkdir();binary=Path('/app/bin/typeahead-tantivy')
    result=adapter.import_snapshot(Path('/repo'),source,workspace,binary,timeout=30,environment={'PATH':os.environ['PATH']})
    assert result['emitted_actor_count']==result['source_actor_count']==128
    assert result['partial'] is False and result['source_unchanged'] is True and result['output']=='-'
    assert (workspace/'data/index/meta.json').is_file() and not list(workspace.glob('*.jsonl'))
    try:
        adapter.reconcile_checkpoint(Path('/repo'),workspace,binary,timeout=10,environment={'PATH':os.environ['PATH']},source_bytes=512*1024*1024)
    except RuntimeError as error:
        assert 'stopped before its gates passed' in str(error),str(error)
    else:raise AssertionError('unqualified source was accepted')
    assert 'missing required cloud-only credential variable' in (workspace/'replay.log').read_text()
    check=__import__('subprocess').run([str(binary),'status','--data',str(workspace/'data')],capture_output=True,timeout=10,check=True)
    assert json.loads(check.stdout)['import_complete'] is True
    print(json.dumps({'passed':True,'actors':128,'exported_via_pipe':True,'unready_source_rejected':True,'external_network':False}))
"""


def test_tantivy_automatic_install_is_fenced_before_any_preparation(tmp_path):
    before = list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="automatic installation remains fenced"):
        typeahead_index.fn(
            snapshot_engine="tantivy", snapshot_format="compact", automatic_refresh=True
        )
    assert list(tmp_path.iterdir()) == before


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


def test_actual_native_compact_export_streams_directly_into_rust(tmp_path):
    location = os.environ.get("TA_REPOSITORY")
    if not location:
        pytest.skip("requires the actual Typeahead fixture/exporter checkout")
    repository = Path(location)
    native = repository / "services/zig-out/bin/typeahead-ingester"
    rust = Path(
        os.environ.get(
            "TA_BINARY", repository / "services/tantivy/target/release/typeahead-tantivy"
        )
    )
    if not native.is_file() or not rust.is_file():
        pytest.skip("requires existing native and Rust binaries; no builds performed")
    with native.open("rb") as executable:
        linux_elf = executable.read(4) == b"\x7fELF"
    if linux_elf and platform.system() != "Linux":
        image = os.environ.get("TA_PREPARATION_DOCKER_IMAGE")
        if not image:
            pytest.skip(
                "native fixture is Linux ELF; set TA_PREPARATION_DOCKER_IMAGE to an existing local qualification image"
            )
        from mps import typeahead_tantivy

        module = Path(typeahead_tantivy.__file__).resolve()
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                f"mps-tantivy-preparation-{os.getpid()}",
                "--network",
                "none",
                "--cpus",
                "2",
                "--memory",
                "1g",
                "--entrypoint",
                "python3",
                "-v",
                f"{repository}:/repo:ro",
                "-v",
                f"{module}:/mps_adapter.py:ro",
                "-v",
                f"{module.with_name('typeahead_source_budget.py')}:/typeahead_source_budget.py:ro",
                image,
                "-c",
                LINUX_EXERCISE,
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout.strip().splitlines()[-1])["passed"] is True
        return
    app = tmp_path / "app"
    (app / "bin").mkdir(parents=True)
    (app / "bin/typeahead-ingester").symlink_to(native)
    (app / "input").symlink_to(repository / "services/tantivy-input", target_is_directory=True)
    spec = importlib.util.spec_from_file_location(
        "existing_full_fixture", repository / "deploy/tantivy-qualification/image/full_fixture.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    built = fixture.build(tmp_path / "source-fixture", app=app)
    source = built["source"]
    (source / "manifest.json").write_text(json.dumps(built["manifest"]))
    workspace = tmp_path / "builder"
    workspace.mkdir()
    result = import_snapshot(
        repository,
        source,
        workspace,
        rust,
        timeout=30,
        environment={"PATH": os.environ.get("PATH", "")},
    )
    assert result["emitted_actor_count"] == result["source_actor_count"] == 128
    assert result["partial"] is False and result["source_unchanged"] is True
    assert result["output"] == "-"
    assert (workspace / "data/index/meta.json").is_file()
    assert not list(workspace.glob("*.jsonl"))
    with pytest.raises(RuntimeError, match="stopped before its gates passed"):
        reconcile_checkpoint(
            repository,
            workspace,
            rust,
            timeout=10,
            environment={"PATH": os.environ.get("PATH", "")},
            source_bytes=512 * 1024 * 1024,
        )
    assert (
        "missing required cloud-only credential variable" in (workspace / "replay.log").read_text()
    )
    spec = importlib.util.spec_from_file_location(
        "existing_admission_fixture",
        repository / "scripts/snapshot_refresh/test_tantivy_install.py",
    )
    admission = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(admission)
    with (workspace / "after-failure.log").open("wb") as log:
        child, public, _ = admission.start_strict(
            rust, workspace / "data", log, "offline-no-source"
        )
        try:
            assert admission.smoke.request(public, "/index/status")[2]["import_complete"] is True
        finally:
            admission.smoke.stop(child)

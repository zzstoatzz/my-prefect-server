import logging
import shutil
import subprocess
import sys
import tarfile

import pytest

from flows import typeahead_index as index


@pytest.fixture
def builder(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    services = repo / "services"
    services.mkdir(parents=True)
    (services / "build.zig.zon").write_text("primary")
    monkeypatch.setattr(index, "INDEXER_HOME", tmp_path / "work")
    monkeypatch.setattr(index, "get_run_logger", lambda: logging.getLogger(__name__))
    monkeypatch.delenv("INDEX_QUIET", raising=False)
    monkeypatch.delenv("INDEX_MIN_REMAIN_GIB", raising=False)
    stream = index._stream
    monkeypatch.setattr(
        index,
        "_stream",
        lambda command, cwd, env, timeout: stream(command, cwd, env, min(timeout, 60)),
    )
    return repo, services


def compiler(services, monkeypatch, source):
    path = services / "compiler"
    path.write_text(f"#!{sys.executable}\n" + source)
    path.chmod(0o755)
    monkeypatch.setattr(index, "zig_for", lambda _: str(path))


def test_real_zig_build_uses_vendored_package(builder):
    zig = shutil.which("zig")
    if zig is None:
        pytest.skip("local Zig required for offline dependency integration")
    repo, services = builder
    package = repo.parent / "tiny"
    package.mkdir()
    (package / "build.zig.zon").write_text(
        '.{.name=.tiny,.version="0.0.0",.fingerprint=0x50b897c9b507d131,.paths=.{""}}'
    )
    (package / "build.zig").write_text('pub fn build(_: *@import("std").Build) void {}')
    (package / "payload.txt").write_text("real-vendored-build")
    archive = repo.parent / "tiny.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(package, arcname="tiny")
    fetched = repo.parent / "fetched"
    result = subprocess.run(
        [zig, "fetch", "--pkg-dir", str(fetched), str(archive)],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    package_hash = result.stdout.strip()
    vendor = services / ".zig-pkg-vendor"
    vendor.mkdir()
    shutil.copyfile(archive, vendor / f"{package_hash}.tar.gz")
    (services / "build.zig.zon").write_text(
        '.{.name=.tiny,.version="0.0.0",.fingerprint=0x50b897c9b507d131,.paths=.{""},'
        '.dependencies=.{.tiny=.{.url="https://127.0.0.1:9/unavailable.tar.gz",'
        f'.hash="{package_hash}"' + "}}}"
    )
    (services / "build.zig").write_text(
        'pub fn build(b: *@import("std").Build) void {'
        '_ = b.standardOptimizeOption(.{}); const dep=b.dependency("tiny", .{});'
        'const install=b.addInstallFileWithDir(dep.path("payload.txt"),'
        '.bin,"typeahead-ingester");b.getInstallStep().dependOn(&install.step);}'
    )
    binary = index.build_binary.fn(repo)
    assert binary.read_text() == "real-vendored-build"
    seeded = index.INDEXER_HOME / "zig-global-cache" / "p"
    assert (seeded / f"{package_hash}.tar.gz").read_bytes() == archive.read_bytes()


def test_missing_fallback_preserves_primary_process_error(builder, monkeypatch):
    repo, services = builder
    compiler(
        services, monkeypatch, "import sys; print('primary compiler diagnostic'); sys.exit(23)"
    )
    with pytest.raises(index.ProcessExecutionError) as raised:
        index.build_binary.fn(repo)
    assert raised.value.returncode == 23
    assert "primary compiler diagnostic" in raised.value.output
    assert "no services/build.zig.zon.gh" in " ".join(raised.value.__notes__)
    assert (services / "build.zig.zon").read_text() == "primary"


@pytest.mark.parametrize("fallback_success", [True, False])
def test_explicit_legacy_fallback_and_manifest_restoration(builder, monkeypatch, fallback_success):
    repo, services = builder
    (services / "build.zig.zon.gh").write_text("fallback")
    compiler(
        services,
        monkeypatch,
        "from pathlib import Path\nimport sys\n"
        "manifest=Path('build.zig.zon').read_text()\n"
        "print('diagnostic:'+manifest, flush=True)\n"
        "if manifest=='primary': sys.exit(23)\n"
        + ("sys.exit(24)\n" if not fallback_success else "")
        + "out=Path('zig-out/bin/typeahead-ingester'); out.parent.mkdir(parents=True); out.write_text('ok')\n",
    )
    if fallback_success:
        assert index.build_binary.fn(repo).read_text() == "ok"
    else:
        with pytest.raises(index.ProcessExecutionError) as raised:
            index.build_binary.fn(repo)
        assert raised.value.returncode == 24
        assert "diagnostic:fallback" in raised.value.output
        assert raised.value.__cause__.returncode == 23
        assert "diagnostic:primary" in raised.value.__cause__.output
    assert (services / "build.zig.zon").read_text() == "primary"


def test_disk_reserve_failure_does_not_try_legacy_fallback(builder, monkeypatch):
    repo, services = builder
    (services / "build.zig.zon.gh").write_text("fallback")
    monkeypatch.setenv("INDEX_MIN_REMAIN_GIB", "999999999")
    monkeypatch.setenv("INDEX_BUILD_ROOT", str(services))
    compiler(services, monkeypatch, "raise AssertionError('must not run')")
    with pytest.raises(RuntimeError, match="disk reserve"):
        index.build_binary.fn(repo)
    assert (services / "build.zig.zon").read_text() == "primary"

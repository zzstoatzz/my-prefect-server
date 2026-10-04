import platform

from mps.zig_toolchain import minimum_zig_version, zig_for


def _project(tmp_path, zon: str | None):
    build_dir = tmp_path / "project"
    build_dir.mkdir()
    if zon is not None:
        (build_dir / "build.zig.zon").write_text(zon)
    return build_dir


def _install(opt, version: str):
    zig = opt / f"zig-{platform.machine()}-linux-{version}" / "zig"
    zig.parent.mkdir(parents=True)
    zig.write_text("")
    return zig


def test_reads_minimum_version(tmp_path):
    build_dir = _project(tmp_path, '.{\n    .name = .x,\n    .minimum_zig_version = "0.17.0",\n}\n')
    assert minimum_zig_version(build_dir) == "0.17.0"


def test_picks_the_matching_install(tmp_path):
    build_dir = _project(tmp_path, '.{ .minimum_zig_version = "0.17.0" }')
    opt = tmp_path / "opt"
    _install(opt, "0.16.0")
    wanted = _install(opt, "0.17.0")
    assert zig_for(build_dir, opt) == str(wanted)


def test_falls_back_to_path_when_version_is_not_installed(tmp_path):
    build_dir = _project(tmp_path, '.{ .minimum_zig_version = "0.18.0" }')
    opt = tmp_path / "opt"
    _install(opt, "0.17.0")
    assert zig_for(build_dir, opt) == "zig"


def test_falls_back_to_path_without_a_declared_version(tmp_path):
    assert zig_for(_project(tmp_path, ".{ .name = .x }"), tmp_path) == "zig"


def test_falls_back_to_path_without_a_manifest(tmp_path):
    assert zig_for(_project(tmp_path, None), tmp_path) == "zig"

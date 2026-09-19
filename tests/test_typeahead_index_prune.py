"""typeahead-index build retention: keep the newest builds and the serving one."""

from pathlib import Path

from flows.typeahead_index import KEEP_BUILDS, builds_to_prune, prune_builds


def _dirs(*ids: str) -> list[Path]:
    return [Path(f"/x/build-{i}") for i in ids]


def test_keeps_newest_and_serving():
    dirs = _dirs("b100-aa", "b200-bb", "b300-cc", "b400-dd")
    victims = builds_to_prune(dirs, serving="b400-dd")
    assert [v.name for v in victims] == ["build-b200-bb", "build-b100-aa"]


def test_serving_older_than_window_is_retained():
    dirs = _dirs("b100-aa", "b200-bb", "b300-cc", "b400-dd")
    victims = builds_to_prune(dirs, serving="b100-aa")
    assert [v.name for v in victims] == ["build-b200-bb"]


def test_unknown_serving_prunes_nothing():
    assert builds_to_prune(_dirs("b100-aa", "b200-bb", "b300-cc"), serving=None) == []


def test_keep_window_is_two():
    assert KEEP_BUILDS == 2


def test_prune_builds_deletes_on_disk_and_reports_bytes(tmp_path, monkeypatch):
    for i in ("b100-aa", "b200-bb", "b300-cc"):
        d = tmp_path / f"build-{i}"
        d.mkdir()
        (d / "index.db").write_bytes(b"x" * 1024)
    (tmp_path / "not-a-build").mkdir()
    monkeypatch.setattr("flows.typeahead_index.serving_build_id", lambda: "b300-cc")
    freed = prune_builds.fn(str(tmp_path))
    assert freed == 1024
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "build-b200-bb",
        "build-b300-cc",
        "not-a-build",
    ]


def test_prune_builds_skips_when_service_unreachable(tmp_path, monkeypatch):
    for i in ("b100-aa", "b200-bb", "b300-cc"):
        (tmp_path / f"build-{i}").mkdir()
    monkeypatch.setattr("flows.typeahead_index.serving_build_id", lambda: None)
    assert prune_builds.fn(str(tmp_path)) == 0
    assert len(list(tmp_path.iterdir())) == 3

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
    monkeypatch.setattr(
        "mps.typeahead_refresh.protected_source_build_ids", lambda logger: {"b300-cc"}
    )
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
    monkeypatch.setattr("mps.typeahead_refresh.protected_source_build_ids", lambda logger: None)
    assert prune_builds.fn(str(tmp_path)) == 0
    assert len(list(tmp_path.iterdir())) == 3


def test_both_formats_count_as_one_generation():
    paths = _dirs("b100-aa", "b100-aa-px05", "b200-bb", "b200-bb-px05", "b300-cc", "b300-cc-px05")
    assert {p.name for p in builds_to_prune(paths, "b300-cc-px05")} == {
        "build-b100-aa",
        "build-b100-aa-px05",
    }


def test_blocked_retention_is_not_silent_success(tmp_path, monkeypatch):
    import pytest

    for i in range(5):
        (tmp_path / f"build-b{i}-aa").mkdir()
    monkeypatch.setattr("mps.typeahead_refresh.protected_source_build_ids", lambda logger: None)
    with pytest.raises(RuntimeError, match="retention blocked"):
        prune_builds.fn(str(tmp_path))
    assert len(list(tmp_path.iterdir())) == 5


def test_real_freshness_http_responses():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from flows.typeahead_index import serving_build_id

    build = "b1792908655-14d5-px05"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, body = {
                "/fresh": (200, {"ok": True, "probe_ok": True, "build_id": build}),
                "/stale": (503, {"ok": True, "probe_ok": True, "stale": True, "build_id": build}),
                "/broken": (503, {"ok": False, "probe_ok": False, "build_id": build}),
                "/invalid": (503, {"ok": True, "probe_ok": True, "build_id": "../unsafe"}),
                "/denied": (401, {"ok": True, "probe_ok": True, "build_id": build}),
            }[self.path]
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            assert serving_build_id(base + "/fresh") == build
            assert serving_build_id(base + "/stale") == build
            for path in ["/broken", "/invalid", "/denied"]:
                assert serving_build_id(base + path) is None
        finally:
            server.shutdown()
            thread.join()

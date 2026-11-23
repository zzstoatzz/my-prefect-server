import datetime as dt
import json
from pathlib import Path

import pytest

from flows.backup_freshness import (
    REPO_DR_CONFIG,
    backup_freshness,
    config_drift,
    dump_time,
    problems,
)

NOW = dt.datetime(2026, 10, 27, 7, 5, tzinfo=dt.UTC)
NEWEST = "20261027T054712Z.dump.age"
LOCAL = ["20261026T054530Z.dump.age", NEWEST, ".lock"]
OFFSITE = {"checked_at": "2026-10-27T05:47:40Z", "newest_uploaded": NEWEST}
RESTORE = {"verified_at": "2026-10-26T07:04:00Z", "name": "20261026T054530Z.dump.age"}
HOST = {"checked_at": "2026-10-27T06:16:10Z", "newest_uploaded": "20261027T061555Z.host.tar.age"}


def test_dump_time_parses_backup_stamp():
    assert dump_time(NEWEST) == dt.datetime(2026, 10, 27, 5, 47, 12, tzinfo=dt.UTC)


def test_fresh_backups_have_no_problems():
    assert problems(NOW, LOCAL, OFFSITE, RESTORE, HOST) == []


def test_stale_local_dump():
    found = problems(NOW + dt.timedelta(hours=26), LOCAL, OFFSITE, RESTORE, HOST)
    assert any("newest local dump" in p for p in found)


def test_upload_behind_local():
    behind = {**OFFSITE, "newest_uploaded": "20261026T054530Z.dump.age"}
    assert any("R2 newest" in p for p in problems(NOW, LOCAL, behind, RESTORE, HOST))


def test_missing_records_are_problems():
    found = problems(NOW, [".lock"], None, None)
    assert found == [
        "no local dump",
        "no R2 upload has been recorded",
        "no restore from R2 has been verified",
        "no host state snapshot has been recorded",
    ]


def test_stale_restore_verification():
    found = problems(NOW + dt.timedelta(days=8), LOCAL, OFFSITE, RESTORE, HOST)
    assert any("verified R2 restore" in p for p in found)


def test_stale_host_snapshot():
    stale = {**HOST, "checked_at": "2026-10-25T06:16:10Z"}
    found = problems(NOW, LOCAL, OFFSITE, RESTORE, stale)
    assert found == ["host state snapshot last uploaded at 2026-10-25T06:16:10Z"]


def installed_config(tmp_path, text=None):
    path = tmp_path / "heavypad.toml"
    path.write_text(REPO_DR_CONFIG.read_text() if text is None else text)
    return str(path)


def test_config_drift_flags_missing_and_passes_matching(tmp_path):
    assert config_drift(tmp_path / "heavypad.toml", REPO_DR_CONFIG) is not None
    assert config_drift(Path(installed_config(tmp_path)), REPO_DR_CONFIG) is None


def test_stale_installed_config_is_drift(tmp_path):
    stale = REPO_DR_CONFIG.read_text().replace('"mcp-atlas", ', '"leaflet-atlas", "mcp-atlas", ')
    drift = config_drift(Path(installed_config(tmp_path, stale)), REPO_DR_CONFIG)
    assert drift is not None and "differs from main" in drift


def test_flow_fails_on_problems(tmp_path):
    (tmp_path / "backups").mkdir()
    with pytest.raises(RuntimeError, match="no local dump"):
        backup_freshness.fn(state_dir=str(tmp_path), dr_config=installed_config(tmp_path))


def test_flow_reports_config_drift(tmp_path):
    (tmp_path / "backups").mkdir()
    with pytest.raises(RuntimeError, match="differs from main"):
        backup_freshness.fn(state_dir=str(tmp_path), dr_config=installed_config(tmp_path, "x"))


def test_flow_passes_when_fresh(tmp_path):
    (tmp_path / "backups").mkdir()
    now = dt.datetime.now(dt.UTC)
    name = now.strftime("%Y%m%dT%H%M%SZ") + ".dump.age"
    (tmp_path / "backups" / name).touch()
    at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "offsite.json").write_text(json.dumps({"checked_at": at, "newest_uploaded": name}))
    (tmp_path / "restore-verified.json").write_text(json.dumps({"verified_at": at, "name": name}))
    (tmp_path / "host-backup.json").write_text(
        json.dumps({"checked_at": at, "newest_uploaded": "x.host.tar.age"})
    )
    config = installed_config(tmp_path)
    assert backup_freshness.fn(state_dir=str(tmp_path), dr_config=config) == []

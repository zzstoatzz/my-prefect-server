import datetime as dt
import json

import pytest

from flows.backup_freshness import backup_freshness, dump_time, problems

NOW = dt.datetime(2026, 10, 27, 7, 5, tzinfo=dt.UTC)
NEWEST = "20261027T054712Z.dump.age"
LOCAL = ["20261026T054530Z.dump.age", NEWEST, ".lock"]
OFFSITE = {"checked_at": "2026-10-27T05:47:40Z", "newest_uploaded": NEWEST}
RESTORE = {"verified_at": "2026-10-26T07:04:00Z", "name": "20261026T054530Z.dump.age"}


def test_dump_time_parses_backup_stamp():
    assert dump_time(NEWEST) == dt.datetime(2026, 10, 27, 5, 47, 12, tzinfo=dt.UTC)


def test_fresh_backups_have_no_problems():
    assert problems(NOW, LOCAL, OFFSITE, RESTORE) == []


def test_stale_local_dump():
    found = problems(NOW + dt.timedelta(hours=26), LOCAL, OFFSITE, RESTORE)
    assert any("newest local dump" in p for p in found)


def test_upload_behind_local():
    behind = {**OFFSITE, "newest_uploaded": "20261026T054530Z.dump.age"}
    assert any("R2 newest" in p for p in problems(NOW, LOCAL, behind, RESTORE))


def test_missing_records_are_problems():
    found = problems(NOW, [".lock"], None, None)
    assert found == [
        "no local dump",
        "no R2 upload has been recorded",
        "no restore from R2 has been verified",
    ]


def test_stale_restore_verification():
    found = problems(NOW + dt.timedelta(days=8), LOCAL, OFFSITE, RESTORE)
    assert any("verified R2 restore" in p for p in found)


def test_flow_fails_on_problems(tmp_path):
    (tmp_path / "backups").mkdir()
    with pytest.raises(RuntimeError, match="no local dump"):
        backup_freshness.fn(state_dir=str(tmp_path))


def test_flow_passes_when_fresh(tmp_path):
    (tmp_path / "backups").mkdir()
    now = dt.datetime.now(dt.UTC)
    name = now.strftime("%Y%m%dT%H%M%SZ") + ".dump.age"
    (tmp_path / "backups" / name).touch()
    at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "offsite.json").write_text(json.dumps({"checked_at": at, "newest_uploaded": name}))
    (tmp_path / "restore-verified.json").write_text(json.dumps({"verified_at": at, "name": name}))
    assert backup_freshness.fn(state_dir=str(tmp_path)) == []

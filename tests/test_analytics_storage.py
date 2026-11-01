from unittest import mock

import duckdb
import pytest
from mps import analytics
from mps.analytics import compact_database
from mps.db import write_github_issues, write_phi_observations
from mps.github import IssueOrPR
from mps.phi import PhiObservation


@pytest.fixture(autouse=True)
def no_writer_lease(monkeypatch):
    monkeypatch.delenv("PREFECT_API_URL", raising=False)
    with mock.patch("prefect.settings.PREFECT_API_URL") as setting:
        setting.value.return_value = None
        yield


def slots(db_path, table):
    with duckdb.connect(str(db_path), read_only=True) as db:
        return db.execute(
            "SELECT estimated_size FROM duckdb_tables() WHERE table_name = ?", [table]
        ).fetchone()[0]


def observation(n, content="seen"):
    return PhiObservation(handle="a.bsky.social", observation_id=f"o{n}", content=content)


def test_changed_and_new_rows_are_written_and_unchanged_keep_their_fetch_time(tmp_path):
    db = tmp_path / "analytics.duckdb"
    write_phi_observations([observation(1), observation(2)], str(db))
    with duckdb.connect(str(db)) as con:
        con.execute("UPDATE raw_phi_observations SET fetched_at = '2026-01-01'")
    write_phi_observations([observation(1), observation(2, "changed"), observation(3)], str(db))
    with duckdb.connect(str(db), read_only=True) as con:
        rows = con.execute(
            "SELECT observation_id, content, fetched_at::DATE::VARCHAR "
            "FROM raw_phi_observations ORDER BY 1"
        ).fetchall()
    assert rows[0] == ("o1", "seen", "2026-01-01")
    assert rows[1][:2] == ("o2", "changed") and rows[1][2] != "2026-01-01"
    assert rows[2][:2] == ("o3", "seen")


def test_list_columns_take_part_in_change_detection(tmp_path):
    db = tmp_path / "analytics.duckdb"

    def issue(labels):
        return IssueOrPR(
            repo="o/r",
            number=1,
            type="Issue",
            title="t",
            state="open",
            body="",
            url="u",
            labels=labels,
            created_at="2026-01-01",
            updated_at="2026-01-01",
            user="me",
            comments=0,
            reactions_total=0,
        )

    write_github_issues([issue(["bug"])], str(db))
    write_github_issues([issue(["bug"])], str(db))
    write_github_issues([issue(["bug", "p1"])], str(db))
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute("SELECT labels FROM raw_github_issues").fetchall() == [(["bug", "p1"],)]


def test_compaction_drops_dead_versions_and_obsolete_relations(tmp_path):
    db = tmp_path / "analytics.duckdb"
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE TABLE raw_phi_observations (observation_id VARCHAR PRIMARY KEY, n INT)")
        con.execute("INSERT INTO raw_phi_observations SELECT 'o' || i, i FROM range(250000) t(i)")
        con.execute("CHECKPOINT")
        con.execute("DELETE FROM raw_phi_observations WHERE n >= 5000")
        con.execute("CREATE TABLE raw_likes AS SELECT 1 AS n")
        con.execute("CREATE VIEW stg_likes AS SELECT * FROM raw_likes")
        con.execute("CREATE VIEW stg_kept AS SELECT observation_id FROM raw_phi_observations")
    assert slots(db, "raw_phi_observations") > 5000

    result = compact_database(db)

    assert result["bytes_after"] < result["bytes_before"]
    assert result["rows"] == 5000
    assert slots(db, "raw_phi_observations") == 5000
    with duckdb.connect(str(db)) as con:
        relations = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        assert relations == {"raw_phi_observations", "stg_kept"}
        with pytest.raises(duckdb.ConstraintException):
            con.execute("INSERT INTO raw_phi_observations VALUES ('o1', 1)")
    assert not list(tmp_path.glob("*.compacting*"))


def test_failed_compaction_leaves_the_original_in_place(tmp_path, monkeypatch):
    db = tmp_path / "analytics.duckdb"
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE TABLE raw_github_issues AS SELECT range AS n FROM range(10)")
    original = db.read_bytes()
    real_connect = duckdb.connect

    def failing_copy(database=":memory:", *args, **kwargs):
        con = real_connect(database, *args, **kwargs)
        if database == ":memory:":
            real_execute = con.execute

            class Wrapped:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    con.close()

                def execute(self, sql, *a):
                    if sql.startswith("COPY FROM DATABASE"):
                        raise duckdb.IOException("disk full")
                    return real_execute(sql, *a)

            return Wrapped()
        return con

    monkeypatch.setattr(duckdb, "connect", failing_copy)
    with pytest.raises(duckdb.IOException):
        analytics.compact_database(db)
    assert db.read_bytes() == original
    assert not list(tmp_path.glob("*.compacting*"))

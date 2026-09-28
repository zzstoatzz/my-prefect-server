import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import dagster as dg
import duckdb
import pytest
from mps.analytics import export_hub_db
from prefect.settings import PREFECT_API_URL, temporary_settings

from hub_dagster.assets import build_definitions
from hub_dagster.bridge import submit_and_wait


def test_export_failure_preserves_last_good_database(tmp_path):
    source, published = tmp_path / "source.duckdb", tmp_path / "hub.duckdb"
    with duckdb.connect(str(source)) as db:
        db.execute("CREATE TABLE unrelated AS SELECT 1 AS n")
    published.write_bytes(b"previous export")
    with pytest.raises(duckdb.Error):
        export_hub_db(source, published)
    assert published.read_bytes() == b"previous export"


@pytest.mark.parametrize("status", [dg.DagsterRunStatus.SUCCESS, dg.DagsterRunStatus.FAILURE])
def test_bridge_reuses_terminal_run_and_propagates_failure(tmp_path, monkeypatch, status):
    monkeypatch.setenv("DAGSTER_HOME", str(tmp_path))
    (tmp_path / "dagster.yaml").write_text("telemetry:\n  enabled: false\n")
    run_id = str(uuid4())
    with dg.DagsterInstance.get() as instance:
        instance.create_run_for_job(
            dg.GraphDefinition(name="hub_transform").to_job(), run_id=run_id, status=status
        )
    if status == dg.DagsterRunStatus.SUCCESS:
        submit_and_wait(run_id, timeout=1)
    else:
        with pytest.raises(RuntimeError, match="FAILURE"):
            submit_and_wait(run_id, timeout=1)
    with dg.DagsterInstance.get() as instance:
        assert len(instance.get_runs()) == 1


@pytest.mark.parametrize("fails", [False, True])
def test_real_dbt_build_and_metadata_release_database_before_other_assets(
    tmp_path, monkeypatch, fails
):
    project = tmp_path / "dbt"
    (project / "models").mkdir(parents=True)
    (project / "profiles").mkdir()
    (project / "seeds").mkdir()
    (project / "seeds/example.csv").write_text("n\n1\n")
    (project / "dbt_project.yml").write_text(
        "name: failure_check\nversion: '1.0'\nprofile: failure_check\n"
    )
    database = tmp_path / "analytics.duckdb"
    (project / "profiles/profiles.yml").write_text(
        f"failure_check:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: '{database}'\n      threads: 1\n"
    )
    (project / "models/hub_action_items.sql").write_text(
        "select * from missing_source_table" if fails else "select 1 as n"
    )
    with duckdb.connect(str(database)) as db:
        db.execute("CREATE TABLE raw_github_issues AS SELECT 1 AS n")
        db.execute("CREATE TABLE raw_liked_posts AS SELECT 1 AS n")
    subprocess.run(
        [
            str(Path(sys.executable).with_name("dbt")),
            "parse",
            "--project-dir",
            project.name,
            "--profiles-dir",
            str(project / "profiles"),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("PREFECT_API_URL", "")
    export = tmp_path / "hub.duckdb"
    export.write_bytes(b"last good export")
    defs = build_definitions(project, database, tmp_path / "missing-spend.jsonl", export)
    graph = defs.resolve_asset_graph()
    assert dg.AssetKey("llm_spend") not in graph.get(dg.AssetKey("hub_action_items")).parent_keys
    assert len(graph.get(dg.AssetKey("hub_export")).parent_keys) == 3
    with temporary_settings({PREFECT_API_URL: None}):
        result = defs.resolve_job_def("hub_transform").execute_in_process(raise_on_error=False)
    if fails:
        assert not result.success
        assert any(
            event.event_type_value == "STEP_FAILURE"
            and "missing_source_table" in str(event.event_specific_data)
            for event in result.all_events
        )
        assert export.read_bytes() == b"last good export"
        assert not any(
            e.asset_key == dg.AssetKey("hub_export")
            for e in result.get_asset_materialization_events()
        )
    else:
        assert result.success
        with duckdb.connect(str(export), read_only=True) as db:
            assert db.execute("SELECT * FROM hub_action_items").fetchall() == [(1,)]
        events = result.get_asset_materialization_events()
        model = next(e for e in events if e.asset_key == dg.AssetKey("hub_action_items"))
        assert "dagster/column_schema" in model.event_specific_data.materialization.metadata


def test_phi_top_tags_break_ties_independent_of_input_order():
    model = Path("analytics/models/enrichment/int_phi_user_profiles.sql").read_text()
    query = model.replace("{{ ref('stg_phi_observations') }}", "observations").replace(
        "{{ ref('stg_phi_interactions') }}", "interactions"
    )
    for tags in (["z", "y", "x", "w", "v", "u"], ["u", "v", "w", "x", "y", "z"]):
        with duckdb.connect() as db:
            db.execute(
                "CREATE TABLE observations(handle VARCHAR, observation_id VARCHAR, tags VARCHAR[], created_at TIMESTAMP)"
            )
            db.execute("CREATE TABLE interactions(handle VARCHAR, created_at TIMESTAMP)")
            db.executemany(
                "INSERT INTO observations VALUES ('subject', ?, [?], '2026-09-28')",
                [(tag, tag) for tag in tags],
            )
            result = db.execute(query).fetchone()
            assert result[5] == ["u", "v", "w", "x", "y"]


def test_snapshot_validation_uses_its_own_catalog_and_preserves_source_project(tmp_path):
    project = tmp_path / "analytics"
    (project / "models").mkdir(parents=True)
    (project / "profiles").mkdir()
    (project / "dbt_project.yml").write_text("name: snapshot\nversion: '1.0'\nprofile: snapshot\n")
    (project / "profiles/profiles.yml").write_text(
        "snapshot:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
        "      path: \"{{ env_var('ANALYTICS_DB_PATH') }}\"\n      threads: 1\n"
    )
    (project / "models/hub_action_items.sql").write_text("select 1 as n")
    database = tmp_path / "production.duckdb"
    with duckdb.connect(str(database)) as db:
        db.execute("CREATE TABLE raw_github_issues AS SELECT 1 AS n")
        db.execute("CREATE TABLE raw_liked_posts AS SELECT 1 AS n")
    spend = tmp_path / "spend.jsonl"
    spend.write_text("")
    env = {
        **os.environ,
        "ANALYTICS_DB_PATH": str(database),
        "LLM_SPEND_LOG_PATH": str(spend),
        "PREFECT_API_URL": "",
    }
    report_dir = tmp_path / "validation"
    result = subprocess.run(
        [
            sys.executable,
            str(Path("deploy/dagster/validate_snapshot.py").resolve()),
            str(report_dir),
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((report_dir / "verification.json").read_text())
    assert report["equal_row_counts"] == {"hub_action_items": 1, "raw_llm_spend": 0}
    assert "dagster/column_schema" in report["materialization_metadata"]["hub_action_items"]
    assert not (project / "target").exists()

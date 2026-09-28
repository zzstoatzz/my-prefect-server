"""Compare the complete dbt build with Dagster on one locally retained snapshot."""

import fcntl
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
from mps.analytics import HUB_TABLES, export_hub_db, import_spend_log
from mps.lock import analytics_write_slot


def main():
    root = Path(sys.argv[1]).resolve()
    resume = "--resume" in sys.argv[2:]
    root.mkdir(mode=0o700, parents=True, exist_ok=resume)
    production = Path(os.environ["ANALYTICS_DB_PATH"])
    spend = Path(os.environ["LLM_SPEND_LOG_PATH"])
    baseline = root / "baseline.duckdb"
    shadow = root / "shadow.duckdb"
    source_project = Path("analytics").resolve()
    project = root / "project"
    shutil.copytree(
        source_project,
        project,
        ignore=shutil.ignore_patterns("target", "logs"),
        dirs_exist_ok=True,
    )
    if not resume:
        with analytics_write_slot():
            with duckdb.connect(str(production)) as db:
                db.execute("CHECKPOINT")
            shutil.copyfile(production, baseline)
            shutil.copyfile(production, shadow)
            with spend.open("rb") as source, (root / "spend.jsonl").open("wb") as target:
                fcntl.flock(source.fileno(), fcntl.LOCK_SH)
                shutil.copyfileobj(source, target)
        import_spend_log(root / "spend.jsonl", baseline)
        env = {**os.environ, "ANALYTICS_DB_PATH": str(baseline)}
        subprocess.run(
            [
                str(Path(sys.executable).with_name("dbt")),
                "build",
                "--project-dir",
                str(project),
                "--profiles-dir",
                str(project / "profiles"),
                "--target-path",
                str(root / "baseline-target"),
            ],
            env=env,
            check=True,
        )
        export_hub_db(baseline, root / "baseline-hub.duckdb")
    else:
        baseline_result = json.loads((root / "baseline-target/run_results.json").read_text())
        assert all(r["status"] == "success" for r in baseline_result["results"])
        assert (root / "baseline-hub.duckdb").is_file()
    os.environ["ANALYTICS_DB_PATH"] = str(shadow)
    os.environ["PREFECT_API_URL"] = ""
    subprocess.run(
        [
            str(Path(sys.executable).with_name("dbt")),
            "parse",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(project / "profiles"),
        ],
        check=True,
    )
    import dagster as dg

    from hub_dagster.assets import build_definitions

    definitions = build_definitions(
        project, shadow, root / "spend.jsonl", root / "shadow-hub.duckdb"
    )
    instance_dir = root / "dagster"
    instance_dir.mkdir(exist_ok=True)
    (instance_dir / "dagster.yaml").write_text("telemetry:\n  enabled: false\n")
    with dg.DagsterInstance.local_temp(str(instance_dir)) as instance:
        result = definitions.resolve_job_def("hub_transform").execute_in_process(instance=instance)
        assert result.success
        models = json.loads((project / "target/manifest.json").read_text())["nodes"]
        names = sorted(
            n["alias"] for n in models.values() if n["resource_type"] in {"model", "seed"}
        )
        checks = {}
        with duckdb.connect(str(baseline), read_only=True) as db:
            db.execute(f"ATTACH '{shadow}' AS shadow (READ_ONLY)")
            for name in [*names, "raw_llm_spend"]:
                table = '"' + name.replace('"', '""') + '"'
                difference = db.execute(
                    f"SELECT count(*) FROM ((SELECT * FROM main.{table} EXCEPT ALL SELECT * FROM shadow.main.{table}) UNION ALL (SELECT * FROM shadow.main.{table} EXCEPT ALL SELECT * FROM main.{table}))"
                ).fetchone()[0]
                assert difference == 0, f"{name}: {difference} differing rows"
                checks[name] = db.execute(f"SELECT count(*) FROM main.{table}").fetchone()[0]
        metadata = {}
        for name in names:
            event = instance.get_latest_materialization_event(dg.AssetKey(name))
            assert event is not None, f"missing materialization for {name}"
            keys = sorted(event.asset_materialization.metadata)
            assert "dagster/column_schema" in keys, f"missing schema for {name}"
            metadata[name] = keys
        with duckdb.connect(str(root / "shadow-hub.duckdb"), read_only=True) as db:
            assert {r[0] for r in db.execute("SHOW TABLES").fetchall()} == set(HUB_TABLES)
            db.execute(f"ATTACH '{root / 'baseline-hub.duckdb'}' AS baseline_hub (READ_ONLY)")
            for name in HUB_TABLES:
                difference = db.execute(
                    f"SELECT count(*) FROM ((SELECT * FROM main.{name} EXCEPT ALL SELECT * FROM baseline_hub.main.{name}) UNION ALL (SELECT * FROM baseline_hub.main.{name} EXCEPT ALL SELECT * FROM main.{name}))"
                ).fetchone()[0]
                assert difference == 0, f"export {name}: {difference} differing rows"
        report = {
            "run_id": result.run_id,
            "equal_row_counts": checks,
            "materialization_metadata": metadata,
        }
        (root / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))


if __name__ == "__main__":
    main()

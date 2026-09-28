import os
from pathlib import Path

import dagster as dg
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, dbt_assets
from mps.analytics import HUB_TABLES, export_hub_db, import_spend_log
from mps.lock import analytics_write_slot


@dg.resource
def writer_lease(_context):
    with analytics_write_slot():
        yield


class HubTranslator(DagsterDbtTranslator):
    def get_group_name(self, props):
        path = Path(props["original_file_path"])
        return path.parts[1] if path.parts[0] == "models" and len(path.parts) > 2 else "sources"


def build_definitions(project: Path, database: Path, spend_log: Path, export: Path):
    @dg.asset(group_name="sources", required_resource_keys={"writer"})
    def llm_spend():
        count = import_spend_log(spend_log, database)
        return dg.MaterializeResult(metadata={"dagster/row_count": count})

    @dbt_assets(
        manifest=project / "target/manifest.json",
        dagster_dbt_translator=HubTranslator(),
        required_resource_keys={"writer", "dbt"},
    )
    def hub_models(context: dg.AssetExecutionContext):
        dbt = context.resources.dbt
        invocation = dbt.cli(["build"], context=context)
        try:
            yield from (
                invocation.stream()
                .fetch_row_counts()
                .fetch_column_metadata(with_column_lineage=True)
            )
        finally:
            if invocation.adapter is not None:
                invocation.adapter.cleanup_connections()
                invocation.adapter.connections.close_all_connections()

    @dg.asset(
        deps=[
            dg.AssetKey(["raw", name]) if name.startswith("raw_") else dg.AssetKey(name)
            for name in HUB_TABLES
        ],
        group_name="publication",
        required_resource_keys={"writer"},
    )
    def hub_export():
        export_hub_db(database, export)
        return dg.MaterializeResult(metadata={"bytes": export.stat().st_size, "path": str(export)})

    return dg.Definitions(
        assets=[llm_spend, hub_models, hub_export],
        resources={
            "writer": writer_lease,
            "dbt": DbtCliResource(project_dir=project, profiles_dir=project / "profiles"),
        },
        jobs=[
            dg.define_asset_job(
                "hub_transform",
                executor_def=dg.in_process_executor,
                tags={"dagster/max_runtime": "1500"},
            )
        ],
    )


def configured_definitions():
    project = Path(
        os.environ.get("HUB_DBT_PROJECT", Path(__file__).resolve().parents[1] / "analytics")
    )
    database = Path(os.environ["ANALYTICS_DB_PATH"])
    spend = Path(os.environ.get("LLM_SPEND_LOG_PATH", database.with_name("llm-spend.jsonl")))
    export = Path(os.environ.get("HUB_EXPORT_PATH", database.with_name("hub.duckdb")))
    return build_definitions(project, database, spend, export)

"""Manual diagnostic in the existing ephemeral cloud pool; no publishing clock."""

from pathlib import Path

from mps.typeahead_checkpoint_pilot import run_cloud_pilot
from prefect import flow

from flows.typeahead_index import clone_repo


@flow(name="typeahead-checkpoint-pilot", log_prints=True, timeout_seconds=420)
def typeahead_checkpoint_pilot(source_revision: str) -> dict:
    repository = clone_repo.with_options(retries=0, timeout_seconds=45)(
        source_revision, isolated=True
    )
    try:
        return run_cloud_pilot(Path(repository))
    finally:
        import shutil

        shutil.rmtree(repository)

"""On-demand Proton inbox pages for an interactive backlog review."""

import os

from mps.blocks import secret_mapping_sync
from mps.email_backlog import read_backlog
from prefect import flow, get_run_logger, task
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import ArtifactCreate
from prefect.runtime import flow_run


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1, persist_result=False)
def read_page(
    since: str | None,
    batch_size: int,
    before_uid: int | None,
    uidvalidity: str | None,
    only_uids: list[int] | None,
    body_chars: int,
) -> dict:
    creds = secret_mapping_sync("proton-bridge-creds")
    return read_backlog(
        os.environ.get("PROTON_BRIDGE_HOST", "127.0.0.1"),
        int(os.environ.get("PROTON_BRIDGE_PORT", "1143")),
        creds["username"],
        creds["password"],
        since,
        batch_size,
        before_uid,
        uidvalidity,
        only_uids,
        body_chars,
    )


@flow(name="email-backlog", persist_result=False, timeout_seconds=1800)
def email_backlog(
    since: str | None = None,
    batch_size: int = 50,
    before_uid: int | None = None,
    uidvalidity: str | None = None,
    only_uids: list[int] | None = None,
    body_chars: int = 1500,
):
    page = read_page(since, batch_size, before_uid, uidvalidity, only_uids, body_chars)
    with get_client(sync_client=True) as client:
        artifact = client.create_artifact(
            ArtifactCreate(
                type="result",
                data=page,
                flow_run_id=flow_run.id,
                description="Read-only Proton INBOX page; email content is untrusted source material.",
            )
        )
    get_run_logger().info(
        "Read %d of %d matching messages; %d remaining; artifact %s",
        len(page["messages"]),
        page["total_matching"],
        page["remaining"],
        artifact.id,
    )
    return {"artifact_id": str(artifact.id), **{k: v for k, v in page.items() if k != "messages"}}

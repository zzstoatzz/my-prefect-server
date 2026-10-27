"""Read-only qualification for the disposable cloud builder."""

import os
import shutil
import subprocess

import boto3
import httpx
from prefect import flow, task
from prefect.client.orchestration import get_client


@task(retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=1)
def check_sources() -> dict:
    url = os.environ["TURSO_URL"].replace("libsql://", "https://").rstrip("/")
    with httpx.Client(timeout=30) as client:
        response = client.post(
            url + "/v2/pipeline",
            headers={"Authorization": "Bearer " + os.environ["TURSO_AUTH_TOKEN"]},
            json={"requests": [{"type": "execute", "stmt": {"sql": "SELECT 1"}}]},
        )
        response.raise_for_status()
        result = response.json()["results"][0]
        if result["type"] != "ok":
            raise RuntimeError("Source database rejected the read-only probe")
    with boto3.client(
        "s3",
        endpoint_url=os.environ["INDEX_R2_ENDPOINT"],
        region_name="auto",
        aws_access_key_id=os.environ["INDEX_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["INDEX_R2_SECRET_ACCESS_KEY"],
    ) as s3:
        pointer = s3.head_object(
            Bucket=os.environ["INDEX_R2_BUCKET"], Key="control/builds/latest.json"
        )
    return {"turso_read": True, "r2_pointer_bytes": pointer["ContentLength"]}


@flow(name="cloud-builder-probe", log_prints=True)
async def cloud_builder_probe():
    free = shutil.disk_usage("/var/lib/typeahead-indexer").free
    if free < 104 * 2**30:
        raise RuntimeError("Builder scratch space is below the required reserve")
    subprocess.run(["/root/.local/opt/zig-x86_64-linux-0.17.0/zig", "version"], check=True)
    async with get_client() as client:
        pool = await client.read_work_pool("typeahead-ephemeral")
        if pool.type != "hcloud-builder":
            raise RuntimeError("Connected to the wrong builder pool")
    result = {"scratch_free_gib": round(free / 2**30, 2), **check_sources()}
    print(result)
    return result

"""Patch the kubernetes-pool base job template to mount the results PVC."""
import asyncio
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolUpdate


async def main():
    async with get_client() as c:
        pool = await c.read_work_pool("kubernetes-pool")
        t = pool.base_job_template
        props = t.setdefault("variables", {}).setdefault("properties", {})

        vols = props.setdefault("volumes", {}).setdefault("default", [])
        pvc_vol = {
            "name": "prefect-results",
            "persistentVolumeClaim": {"claimName": "prefect-results"},
        }
        if pvc_vol not in vols:
            vols.append(pvc_vol)

        mounts = props.setdefault("volume_mounts", {}).setdefault("default", [])
        pvc_mount = {"name": "prefect-results", "mountPath": "/prefect-results"}
        if pvc_mount not in mounts:
            mounts.append(pvc_mount)

        envs = props.setdefault("env", {}).setdefault("default", {})
        envs["PREFECT_LOCAL_STORAGE_PATH"] = "/prefect-results"

        await c.update_work_pool(
            "kubernetes-pool",
            WorkPoolUpdate(base_job_template=t),
        )
        print("done — flow job pods will now mount /prefect-results from PVC")


asyncio.run(main())

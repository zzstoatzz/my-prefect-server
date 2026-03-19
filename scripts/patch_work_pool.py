"""Patch the kubernetes-pool base job template to mount the results PVC and analytics hostPath."""
import asyncio
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolUpdate


async def main():
    async with get_client() as c:
        pool = await c.read_work_pool("kubernetes-pool")
        t = pool.base_job_template

        # 1. patch job_manifest to reference {{ volumes }} and {{ volume_mounts }}
        pod_spec = t["job_configuration"]["job_manifest"]["spec"]["template"]["spec"]
        pod_spec["volumes"] = "{{ volumes }}"
        container = pod_spec["containers"][0]
        container["volumeMounts"] = "{{ volume_mounts }}"

        # 2. patch variables with defaults for volumes, volume_mounts, env
        props = t.setdefault("variables", {}).setdefault("properties", {})

        vols = props.setdefault("volumes", {}).setdefault("default", [])
        # prefect result cache PVC
        pvc_vol = {
            "name": "prefect-results",
            "persistentVolumeClaim": {"claimName": "prefect-results"},
        }
        if pvc_vol not in vols:
            vols.append(pvc_vol)
        # shared analytics hostPath — same path Grafana mounts (cross-namespace safe on single-node k3s)
        analytics_vol = {
            "name": "analytics",
            "hostPath": {"path": "/var/lib/prefect-analytics", "type": "DirectoryOrCreate"},
        }
        if analytics_vol not in vols:
            vols.append(analytics_vol)

        mounts = props.setdefault("volume_mounts", {}).setdefault("default", [])
        pvc_mount = {"name": "prefect-results", "mountPath": "/prefect-results"}
        if pvc_mount not in mounts:
            mounts.append(pvc_mount)
        analytics_mount = {"name": "analytics", "mountPath": "/prefect-analytics"}
        if analytics_mount not in mounts:
            mounts.append(analytics_mount)

        envs = props.setdefault("env", {}).setdefault("default", {})
        envs["PREFECT_LOCAL_STORAGE_PATH"] = "/prefect-results"
        envs["ANALYTICS_DB_PATH"] = "/prefect-analytics/analytics.duckdb"

        await c.update_work_pool(
            "kubernetes-pool",
            WorkPoolUpdate(base_job_template=t),
        )
        print("done — flow job pods will now mount /prefect-results (PVC) and /prefect-analytics (hostPath)")


asyncio.run(main())

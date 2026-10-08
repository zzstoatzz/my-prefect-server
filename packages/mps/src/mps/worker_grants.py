from pathlib import Path
from urllib.parse import urlsplit

from prefect.client.schemas.objects import FlowRun

from mps.inference_grants import InferenceGrants
from mps.inference_models import resolve_inference_model


def grant_environment(*, database: Path, inference_url: str):
    """Per-attempt inference grant callbacks for a worker that owns the grant store."""
    endpoint = urlsplit(inference_url)
    if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username:
        raise ValueError("Inference endpoint must use HTTPS")
    grants = InferenceGrants(database)

    async def environment(attempt: str, timeout: int, flow_run: FlowRun) -> dict[str, str]:
        agent = flow_run.parameters.get("agent") or {}
        if not isinstance(agent, dict):
            raise ValueError("Invalid agent configuration")
        selected = resolve_inference_model(agent.get("model"))
        token = grants.acquire(
            attempt,
            model=selected.name,
            lifetime=min(timeout + 300, 86400),
            request_limit=32,
        )
        return {
            "PHI_INFERENCE_URL": inference_url,
            "PHI_INFERENCE_TOKEN": token,
            "PHI_INFERENCE_MODEL": selected.name,
        }

    async def release(attempt: str) -> None:
        grants.revoke(attempt)

    return environment, release

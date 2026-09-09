"""Authorize a small workflow-request surface without exposing Prefect admin access."""

import base64
import hashlib
import hmac
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID

from mps.inference_bridge import NoRedirect


class WorkflowRequests:
    def __init__(
        self,
        *,
        token: str | None = None,
        token_block: str | None = None,
        prefect_url: str,
        prefect_auth: str,
        deployments: dict[str, str],
    ):
        if ":" not in prefect_auth or urlsplit(prefect_url).scheme != "https":
            raise ValueError("Workflow requests require configured credentials and HTTPS")
        self._prefect_authorization = "Basic " + base64.b64encode(prefect_auth.encode()).decode()
        self._base = prefect_url.rstrip("/")
        self._deployments = {name: str(UUID(value)) for name, value in deployments.items()}
        self._opener = build_opener(ProxyHandler({}), NoRedirect())
        if token is None and token_block:
            document = self._api(
                f"/block_types/slug/secret/block_documents/name/{quote(token_block, safe='')}?include_secrets=true"
            )
            token = document["data"]["value"]
        if not isinstance(token, str) or not token:
            raise ValueError("Workflow request credential is missing")
        self._authorization = "Bearer " + token

    def authorized(self, authorization: str) -> bool:
        return hmac.compare_digest(authorization.encode(), self._authorization.encode())

    def _api(self, path: str, payload: dict | None = None) -> dict:
        request = Request(
            self._base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "Authorization": self._prefect_authorization,
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=20) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError("Prefect did not confirm the workflow request") from exc

    def request(self, body: dict) -> dict:
        fields = {"workflow", "instructions", "repo", "request_key", "title", "body"}
        if not isinstance(body, dict) or body.keys() - fields:
            raise ValueError("Unsupported workflow request fields")
        workflow = body.get("workflow")
        if workflow not in self._deployments or workflow not in {"investigate", "propose-change"}:
            raise ValueError("Workflow is not enabled")
        for field in ("instructions", "request_key"):
            value = body.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 32000:
                raise ValueError("Instructions and request key are required")
        repo = body.get("repo")
        if repo not in {None, "my-prefect-server", "find-bufo", "plyr.fm", "bot"}:
            raise ValueError("Unsupported repository")
        if workflow == "investigate":
            parameters = {
                "prompt": body["instructions"],
                "workspace": {"repo": repo},
                "agent": {"provider": "aperture", "tool_mode": "read-only"},
            }
        else:
            if repo is None or any(
                not isinstance(body.get(k), str) or not body[k].strip() for k in ("title", "body")
            ):
                raise ValueError("A proposed change requires a repository, title, and body")
            parameters = {
                "task": body["instructions"],
                "repo": repo,
                "title": body["title"],
                "body": body["body"],
                "requested_by": "phi",
            }
        deployment_id = self._deployments[workflow]
        deployment = self._api(f"/deployments/{deployment_id}")
        if deployment.get("work_pool_name") != "phi-sprites-spike":
            raise ValueError("Workflow is not configured for the Sprite worker")
        identity = json.dumps([body["request_key"], deployment_id, parameters], sort_keys=True)
        run = self._api(
            f"/deployments/{deployment_id}/create_flow_run",
            {
                "parameters": parameters,
                "tags": ["phi"],
                "idempotency_key": "phi-workflow-" + hashlib.sha256(identity.encode()).hexdigest(),
            },
        )
        return {"queued": True, "flow_run_id": run["id"], "name": run["name"], "workflow": workflow}

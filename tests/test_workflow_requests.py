import json
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from mps.inference_bridge import inference_bridge
from mps.inference_grants import InferenceGrants
from mps.workflow_requests import WorkflowRequests


@pytest.fixture
def requests():
    gateway = WorkflowRequests(
        token="request-test-token",
        prefect_url="https://prefect.example/api",
        prefect_auth="backend:credential",
        deployments={"investigate": "c95a7dd0-486d-4f07-b885-bd991f38cb75"},
    )
    gateway._api = Mock(
        side_effect=lambda path, payload=None: (
            {"work_pool_name": "phi-sprites-spike"}
            if payload is None
            else {"id": "run-id", "name": "run-name"}
        )
    )
    return gateway


def request_body(**changes):
    return {
        "workflow": "investigate",
        "instructions": "Explain the failure",
        "request_key": "one",
        **changes,
    }


def test_request_cannot_select_command_credentials_or_capabilities(requests):
    for field in ("job_variables", "env", "command", "agent", "deployment_id"):
        with pytest.raises(ValueError):
            requests.request(request_body(**{field: "untrusted"}))
    requests._api.assert_not_called()


def test_server_owns_capability_selection_and_retry_identity(requests):
    assert requests.request(request_body())["flow_run_id"] == "run-id"
    payload = requests._api.call_args.args[1]
    assert payload["parameters"]["agent"] == {"provider": "aperture", "tool_mode": "read-only"}
    requests.request(request_body())
    assert requests._api.call_args.args[1]["idempotency_key"] == payload["idempotency_key"]


def test_unconfigured_workflow_and_wrong_pool_are_refused(requests):
    with pytest.raises(ValueError):
        requests.request(request_body(workflow="propose-change"))
    requests._api.assert_not_called()
    requests._api.side_effect = None
    requests._api.return_value = {"work_pool_name": "home-pool"}
    with pytest.raises(ValueError):
        requests.request(request_body())
    assert requests._api.call_count == 1


def test_inference_grant_cannot_queue_a_workflow(requests, tmp_path):
    grants = InferenceGrants(tmp_path / "grants.sqlite")
    inference_token = grants.issue("attempt", model="openai/gpt-5.6-luna")
    with inference_bridge(
        None,
        upstream="http://127.0.0.1:1",
        listen=("127.0.0.1", 0),
        grants=grants,
        workflow_requests=requests,
    ) as state:
        url = f"http://127.0.0.1:{state['port']}/workflows/request"
        body = json.dumps(request_body()).encode()
        with pytest.raises(HTTPError) as error:
            urlopen(Request(url, data=body, headers={"Authorization": "Bearer " + inference_token}))
        assert error.value.code == 401
        requests._api.assert_not_called()
        with urlopen(
            Request(url, data=body, headers={"Authorization": "Bearer request-test-token"})
        ) as response:
            assert json.load(response)["queued"] is True


def test_request_endpoint_cannot_be_mounted_into_agent_namespace(requests, tmp_path):
    with (
        pytest.raises(ValueError, match="agent namespace"),
        inference_bridge(
            tmp_path / "agent.sock", upstream="http://127.0.0.1:1", workflow_requests=requests
        ),
    ):
        pass

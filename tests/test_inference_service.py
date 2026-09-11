from io import BytesIO
from unittest.mock import Mock

import pytest
from mps import inference_service


def test_host_loads_existing_key_without_forwarding_prefect_auth(monkeypatch):
    monkeypatch.setenv("PREFECT_API_URL", "https://prefect.example/api")
    monkeypatch.setenv("PREFECT_API_AUTH_STRING", "operator:sentinel")
    opener = Mock()
    opener.open.return_value = BytesIO(b'{"data":{"value":"test-provider-key"}}')
    monkeypatch.setattr(inference_service, "build_opener", lambda *args: opener)
    assert inference_service.load_inference_key("anthropic-api-key") == "test-provider-key"
    request = opener.open.call_args.args[0]
    assert request.full_url.endswith(
        "/secret/block_documents/name/anthropic-api-key?include_secrets=true"
    )
    assert "sentinel" not in request.full_url


def test_credential_failure_does_not_print_response(monkeypatch):
    monkeypatch.setenv("PREFECT_API_URL", "https://prefect.example/api")
    monkeypatch.setenv("PREFECT_API_AUTH_STRING", "operator:sentinel")
    opener = Mock()
    opener.open.side_effect = ValueError("sensitive-upstream-response")
    monkeypatch.setattr(inference_service, "build_opener", lambda *args: opener)
    with pytest.raises(RuntimeError, match="Unable to load inference credential") as error:
        inference_service.load_inference_key("anthropic-api-key")
    assert "sensitive" not in str(error.value)
    assert error.value.__suppress_context__

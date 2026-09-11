import pytest
from mps.inference_models import resolve_inference_model


def test_default_and_explicit_selection(monkeypatch):
    monkeypatch.setenv("PHI_INFERENCE_MODEL", "anthropic/claude-sonnet-5")
    model = resolve_inference_model()
    assert model.api == "anthropic-messages"
    assert model.path == "/v1/messages"
    assert model.wire_name == "claude-sonnet-5"
    assert resolve_inference_model("openai/gpt-5.6-luna").path == "/v1/chat/completions"


@pytest.mark.parametrize("name", ["", "https://untrusted.example/model", "openai/gpt-6-astra"])
def test_unauthorized_model_rejected(name):
    with pytest.raises(ValueError, match="Unsupported inference model"):
        resolve_inference_model(name)


def test_invalid_operator_default_fails_closed(monkeypatch):
    monkeypatch.setenv("PHI_INFERENCE_MODEL", "unknown")
    with pytest.raises(ValueError):
        resolve_inference_model()


def test_pi_configuration_uses_selected_protocol():
    from mps.pi_sandbox import aperture_models

    config = aperture_models(model="anthropic/claude-sonnet-5")["providers"]["aperture"]
    assert config["api"] == "anthropic-messages"
    assert config["models"] == [{"id": "anthropic/claude-sonnet-5", "maxTokens": 8192}]
    assert config["baseUrl"] == "http://127.0.0.1:8888"

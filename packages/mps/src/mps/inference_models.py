"""Trusted model choices shared by inference admission and Pi execution."""

import os
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class InferenceModel:
    name: str
    api: Literal["openai-completions", "anthropic-messages"]
    wire_name: str
    max_output_tokens: int = 8192

    @property
    def path(self) -> str:
        return "/v1/messages" if self.api == "anthropic-messages" else "/v1/chat/completions"


# These are operator-authorized choices, not names supplied by the sandbox.
# Native Anthropic names avoid selecting Aperture's separate token-billing route.
MODELS = {
    model.name: model
    for model in (
        InferenceModel("openai/gpt-5.6-luna", "openai-completions", "openai/gpt-5.6-luna"),
        InferenceModel("openai/gpt-5.6-terra", "openai-completions", "openai/gpt-5.6-terra"),
        InferenceModel("anthropic/claude-haiku-4.5", "anthropic-messages", "claude-haiku-4-5"),
        InferenceModel("anthropic/claude-sonnet-5", "anthropic-messages", "claude-sonnet-5"),
    )
}


def resolve_inference_model(name: str | None = None) -> InferenceModel:
    """Resolve an explicit choice or the operator's configured default."""
    selected = (
        name if name is not None else os.environ.get("PHI_INFERENCE_MODEL", "openai/gpt-5.6-luna")
    )
    try:
        return MODELS[selected]
    except KeyError:
        raise ValueError(f"Unsupported inference model: {selected}") from None

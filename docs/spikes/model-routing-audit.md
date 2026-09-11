# Model routing audit

The operator wants model selection to remain replaceable across Phi and its Pi workflows. Aperture supports configured OpenAI-compatible upstreams, including self-hosted providers; this spike currently restricts that capability in application code.

## Current sources

- `flows/pi_agent.py` and `flows/pi_pr.py`: schema literals restrict the model to `openai/gpt-5.6-luna`.
- `mps/pi.py`: repeats that restriction in `run_pi`; the input classifier constructs Anthropic Haiku directly.
- `mps/sprite_worker.py`: grants Luna regardless of flow parameters. The generic worker callback currently receives only attempt ID and timeout.
- `mps/pi_execution.py`: passes Luna on Pi's command line.
- `mps/pi_sandbox.py`: writes Luna into Pi's models.json, with a fixed 8192 output limit.
- Phi `src/bot/config.py`: main, extraction, and policy models are settings. Their consumers use those settings.
- Phi `src/bot/agent.py`: main agent supplies Anthropic cache settings and a fixed output limit. Cross-provider behavior requires validation, not merely changing the model string.
- Phi `src/bot/tools/images.py`: image model is fixed to gpt-image-1.

## Required implementation contract

Resolve model selection from trusted configuration before issuing the execution grant. Pass that same selection to the grant, Pi models.json, and Pi invocation. An explicit flow model must be authorized, and a mismatch must fail before inference. Model capabilities and output limits must not silently inherit Luna's settings. Keep request endpoint and credentials controlled by the operator.

Audit provider-specific Phi settings separately from model names. Standardizing the classifier, main agent, and image generation on Aperture requires checking their API formats and capabilities; it is not proven by the existing Pi chat-completions run.

No model routing changes have been deployed by this audit.

## September 11 live discovery

Aperture's model catalog advertises native and Aperture-provided OpenAI and
Anthropic names. A catalog entry is not proof of authorized inference:
`/v1/messages` with `claude-haiku-4-5` returned 401 (missing x-api-key; a
placeholder returned invalid x-api-key). The separate
`anthropic/claude-haiku-4.5` route returned 402 requiring a payment method.
No payment method or credential was changed by these probes.

Pi's public provider API supports `anthropic-messages`, `openai-completions`,
and `openai-responses`, including cache retention and provider usage fields.
The application bridge currently accepts only `/v1/chat/completions` and one
model. The Sprite worker issues the grant before the flow runs, but its
`run_environment` callback receives only attempt and timeout. A configurable
flow model alone cannot change the grant safely. Resolve trusted selection
before grant issuance and carry the same selection into Pi's model catalog
and invocation; validate mismatches before requesting inference.

The deployed Pi installer pins 0.84.4. Locally installed newer provider docs
must not be treated as proof of that pinned version's capabilities. Verify
native cache markers and usage parsing against the pinned implementation.

Native Anthropic transport verified with the existing `anthropic-api-key`
Prefect Secret block, loaded only on heavypad. Two identical Haiku requests
through Aperture `/v1/messages` reported:

| Request | Uncached input | Cache write (5m) | Cache read | Output |
| --- | ---: | ---: | ---: | ---: |
| 1 | 9 | 5602 | 0 | 4 |
| 2 | 9 | 0 | 5602 | 4 |

This proves gateway passthrough of native cache controls with a valid upstream
credential. It does not yet prove the deployed bridge/Pi chain or production
Phi cache efficiency. No new payment method was used. The inference service
must resolve this existing block on the host; the key must never enter the
Sprite or its model configuration.

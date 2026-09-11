# Model routing and cache verification

Gardener uses Pi 0.84.4 in a Sprite. The trusted worker resolves the model
before issuing its inference grant; the same logical name reaches the grant,
Pi configuration, command line, and bridge. A mismatch fails before inference.
The Sprite receives a scoped bearer credential, never the upstream key.

## Switching

Set `agent.model` on a `pi-agent` run to `openai/gpt-5.6-luna` or
`anthropic/claude-haiku-4.5`. Omit it to use the worker's `PHI_INFERENCE_MODEL`
default (Luna when unset). The trusted catalog also admits Terra and Sonnet 5;
the measured tool runs below cover Luna and Haiku. Provider changes start an
independent cache: an Anthropic cache cannot be reused by OpenAI.

`mps.inference_models` owns the allowed names, wire names, native API, and
output caps. The local bridge preserves the logical name for grant validation;
the host translates it for the upstream. Anthropic uses `/v1/messages`, OpenAI
uses `/v1/chat/completions`. Native cache fields pass through. Pi records
provider-reported input, output, cache reads and writes in Prefect logs at each
completed assistant message, alongside tool success/failure without tool content.

Aperture provides the upstream routing boundary. It does not make native APIs
identical, share caches across providers, or replace Pi's execution trace.
The Anthropic key is loaded on heavypad from the existing Prefect Secret
`anthropic-api-key`. Aperture's advertised paid Anthropic route returned 402;
we did not enable billing. Native passthrough with the existing key works.

## Deployed verification, September 11

Both read-only Pi runs completed using `read` on Phi's cache source. No public
messages or repository mutations were part of these probes.

| Provider | Prefect run | First response cache read | Second response cache read |
| --- | --- | ---: | ---: |
| Anthropic Haiku | `ca079d3d-0fdb-47d1-9ab7-3cbdb40f5a1f` | 4,389 | 4,389 |
| OpenAI Luna | `26c70947-08ec-437a-8192-2fb3332b6eb7` | 0 | 3,282 |

Haiku's second response additionally wrote 5,245 cache tokens. Pi's `input`
usage is uncached input; do not add it to Phi's inclusive input count as if
they were the same metric. The deployed package wheel SHA-256 is
`ce5fea3ec5381c09992e95d828f290cc2eae685a8d644b3a4d9aa66599f95632`.

A separate identical-request probe through the deployed host bridge wrote
6,302 Anthropic cache tokens on its first request and read all 6,302 on its
second. Its two-request grant was exhausted and revoked. This verifies native
cache passthrough independently of Pi's prompt construction.

## Phi boundary

Phi still uses PydanticAI directly; switching Gardener does not switch Phi.
Phi's `AGENT_MODEL`, `EXTRACTION_MODEL`, and `POLICY_MODEL` select separate
roles. Its deployed main-agent settings select Anthropic cache TTLs or an
OpenAI cache routing key according to the resolved provider. Moving all of
Phi's inference through Aperture is not proven by Gardener's routing tests.
Image generation remains separate. No voice, memory, or action-policy change
is required to change the transport.

The production cache audit is continuing in the bot repository. In particular,
the old dashboard mixed concurrent runs; its collapse attribution is not a
reliable measure of provider behavior. Use independently correlated Logfire
requests until the corrected recorder has accumulated a fresh window.

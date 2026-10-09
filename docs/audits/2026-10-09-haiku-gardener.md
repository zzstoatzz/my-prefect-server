# Haiku execution diagnosis

## Failure

A disposable Exe atlas coding run ended after three model responses. The last
response used all 8,192 output tokens for reasoning and reported `length`. Pi
exited successfully, and Gardener incorrectly returned an earlier progress
message as the final answer. No patch existed.

The custom Pi model definition omitted `reasoning: true`. In Pi 0.84.4,
`--thinking off` only sends Anthropic's explicit disabled setting for a model
declared reasoning-capable. Haiku 5.5 enables adaptive thinking by default, so
omitting the request setting did not disable it. Adaptive mode also requires
`compat.forceAdaptiveThinking` for this custom provider/model alias.

Source: <https://platform.claude.com/docs/en/models/haiku-5-5/migration-guide>.
Verified against Pi v0.84.4 `packages/ai/src/api/anthropic-messages.ts` and
`packages/coding-agent/docs/models.md`.

## Fix and evidence

- Declare Haiku's reasoning support and adaptive-thinking compatibility.
- Accept final text only from the last assistant response with stop reason
  `stop`; reject truncated or unfinished tool turns even after earlier text.
- Retain usage on failures and expose the terminal stop reason without logging
  prompts, tool contents, or reasoning text.
- Normalize the Gardener alias to the canonical model ID for existing Haiku
  pricing. Estimates remain distinct from actual provider charges.

With these changes injected into a disposable VM, the same atlas task produced
a patch in 49.7 seconds across 16 requests. All responses reported zero reasoning
tokens with thinking off. Estimated inference cost was $0.012744885; 21 frontend
tests passed and Svelte reported zero errors or warnings. This establishes
adapter execution, not complete patch correctness or comparative model quality.

A fresh read-only review took 13.5 seconds and an estimated $0.00354915. Its
useful findings were lost refresh-error visibility and missing tests of actual
async lifecycle behavior. Its other race claims contradicted the candidate's
post-await checks and were excluded from revision instructions.

## Boundaries

Trials use public source in disposable private Exe VMs, with the existing
managed inference integration. The agent has no general network or provider
credentials. Inference is detached before network-enabled dependency installation
and tests; VMs are deleted afterward. No bot commit, pull request, social post,
billing change, merge, or bot deployment is part of this trial.

Revision with low adaptive reasoning completed in 107 seconds across 13
requests, estimated $0.01781962. It extracted the lifecycle into code used by the
page, restored visible refresh errors, and added ten async lifecycle tests.
The trusted test stage passed 31 frontend tests and Svelte checks. The combined
coding, review, and revision estimate is $0.034113655, excluding infrastructure,
failed diagnostic runs, and subsequent validation.

Repository validation passed 627 tests plus lint, types, and frontend checks.
Final regression additions also pass targeted subprocess tests. Haiku's output
allowance is 16,384 tokens to leave room for adaptive reasoning and an answer;
the selected model's allowance is passed explicitly to the local bridge.

Production defaults have not changed. Final review with medium reasoning,
immutable release, and deployment verification remain pending.

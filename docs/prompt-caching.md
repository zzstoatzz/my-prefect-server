# LLM cost — why thousands of calls cost a few dollars

The hub's cost panel shows a call count that dwarfs the dollar figure: several
thousand LLM calls in a window, total spend in the low single digits. That's
not a bug. This note explains it in order of what actually drives the number,
and is honest about prompt caching: it's the deliberate, code-level lever, but
it's the smallest of the factors and on most flows it isn't even engaging.

## 1. cheap model + high call volume (the dominant factor)

Most calls run on **claude-haiku-4-5** at **$1 / $5 per million input/output
tokens** — the cheapest current model — and most are *small, high-frequency*
requests, not big ones. So the bill is driven by **how many calls** there are,
not by per-call size, and the per-call cost on haiku is tiny.

`compact` is the clearest case and the single biggest line item: ~3,800 calls
in a 7-day window (≈10× any other flow) averaging only ~1.2k input tokens each.
Its cost is **call-count-driven** — nothing about caching can touch that. The
sonnet flows (`docket`, `morning`) and embeddings (`text-embedding-3-small`,
cents in total) round out the rest.

## 2. prompt caching — a real win where it engages, a no-op where it can't

Anthropic prices the prompt side in three tiers:

| token kind | price vs. base input |
|---|---|
| uncached input | 1× |
| **cache read** (prefix served from cache) | **0.1×** |
| cache write (prefix written to cache) | 1.25× (5-minute TTL), 2× (1-hour) |

The catch that explains the panel's modest hit rate: **the minimum cacheable
size applies to the cached *prefix*, not the whole request.** For
`anthropic_cache_instructions` the prefix is the system prompt; for
`anthropic_cache_tool_definitions` it's the tool-definition block. If that
prefix is below the model's floor, the setting is a **silent no-op** — zero
cache writes, no error.

The floors are model-specific:

| model | min cacheable prefix |
|---|--:|
| claude-haiku-4-5 | 4096 tokens |
| claude-sonnet-4-6 | 2048 tokens |
| claude-sonnet-4-5 | 1024 tokens |

So a window-wide hit rate of ~10–13% is **correct-low**, not broken. It breaks
down into three cases:

**a. cached prefix under the floor (configured, but a no-op).** The haiku flows
set `anthropic_cache_instructions: "5m"`, but their system prompt sits under the
4096-token haiku floor, so nothing is cached — regardless of total request size:

- `compact` (~1.2k tok/call) — the *whole* request is sub-floor; caching is
  structurally unavailable and no prefix engineering changes that.
- `phi_atlas` (~750 tok/call, a one-line label prompt) — same, trivially.
- `brief` (~16k tok/call) — the request is large, but the large part is the
  day's items (volatile, unique per call, never cacheable); the *constant*
  system-prompt prefix is what would be cached, and it's under the floor.

**b. clears the floor but caching never engages (not configured).** `morning`
runs on sonnet-4-6 at ~15k tok/call — comfortably over the 2048 floor — yet
writes zero cache, because it sets no `anthropic_cache_*` at all. (The phi bot's
`phi-extractor`, sonnet with no cache settings, is the same class.) These are
the only ones worth a code change: add `anthropic_cache_instructions` if the
system prompt clears the floor.

**c. eligible and engaged (caching works).** Where the cached prefix clears the
floor *and* caching is configured, it pays off:

- `docket` (sonnet-4-6, ~3.3k tok/call) clears the 2048 floor → ~30% hit.
- the phi bot's main agent (sonnet, ~26k tok/call, tool defs cached at 1h)
  runs ~4:1 read:write — comfortably past the ~2× write-premium break-even.

**d. multi-turn agents need automatic caching, not just static breakpoints.**
`curate` used to be documented as an engaged haiku/tool-definition cache case,
but live spend data on 2026-06-24 showed 0 cache reads/writes for both
`run_curation_agent` and `run_observation_review`. That means the explicit
instruction/tool breakpoints are still below the haiku floor, or otherwise not
landing where they matter. The right lever is pydantic-ai's Anthropic automatic
cache (`anthropic_cache: "5m"`), which lets Anthropic cache the moving
multi-turn prefix instead of only the tiny static prompt/tool prefix.

Net: caching is genuinely net-positive everywhere it engages; the aggregate is
low because the highest-*volume* flows are sub-floor haiku where it can't.

## where caching is configured

We don't hand-place `cache_control` breakpoints — pydantic-ai's
`AnthropicModelSettings` do it.

| flow (`flows/…`) | model | sets cache | realized | note |
|---|---|---|--:|---|
| `compact.py` | haiku-4-5 | instructions | 0% | prefix < 4096 floor (whole request ~1.2k) |
| `brief.py` | haiku-4-5 | instructions | 0% | system prompt < floor; big volatile payload |
| `phi_atlas.py` | haiku-4-5 | instructions | 0% | one-line prompt, sub-floor |
| `morning.py` | sonnet-4-6 | **none** | 0% | clears floor but caching not configured |
| `docket.py` | sonnet-4-6 | instructions | ~30% | ~3.3k/call clears the 2048 floor |
| `curate.py` | haiku-4-5 | automatic **+** instructions **+** tool defs | verifying | live 2026-06-24 showed explicit breakpoints at 0%; automatic cache is the new lever |

The sibling **phi** bot (`zzstoatzz.io/bot`, the `phi` row in the panel) caches
its ~30 tool definitions (~12k tokens) at a **1-hour** TTL via
`anthropic_cache_tool_definitions="1h"`, covering tool-call loops and
notification bursts within an active period. Its system prompt isn't cached yet
because dynamic per-run context is injected into it and would invalidate the
prefix every run; a planned refactor moves that state into the user message.

### the prefix invariant (why caching works at all)

Caching is a **prefix match**: any byte change in the cached prefix invalidates
everything after it. Render order is `tools` → `system` → `messages`, so caching
instructions covers tools too when tools come first. Keep the system prompt and
tool list **stable** (cached) and put everything that varies between calls — the
question, retrieved rows, timestamps, IDs — *after* the breakpoint. The classic
silent killers (a `datetime.now()` in the system prompt, unsorted `json.dumps`,
a per-user tool set, swapping models mid-run) all live on the volatile side
here, which is what keeps the engaged flows from collapsing to zero.

## measurement (so none of the above is a guess)

`packages/mps/src/mps/spend.py` records `cache_read_tokens`,
`cache_write_tokens`, `provider`, `model`, and the per-tier costs on every call
(from pydantic-ai's usage object). The hub reads the live `llm-spend.jsonl` and
the panel renders the per-window model breakdown and the realized cache-hit rate
(`cache_read / (input + cache_read + cache_write)`). A flow with cache settings
but **zero writes** is sitting under its model's floor; a flow with no settings
that you'd expect to cache is case (b) above.

Pricing tiers, the per-model floors, and prefix mechanics are Anthropic's; see
<https://platform.claude.com/docs/en/build-with-claude/prompt-caching>.

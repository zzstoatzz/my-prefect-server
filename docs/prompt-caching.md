# LLM cost — why thousands of calls cost a few dollars

The hub's cost panel shows a call count that dwarfs the dollar figure: several
thousand LLM calls in a window, total spend in the low single digits. That's
not a bug. This note explains the three things that make it so, in order of how
much they actually matter, so the figure reads as expected rather than
suspicious. Prompt caching is the deliberate, code-level part of the strategy —
but it's the smallest of the three levers, and it's worth being honest about
that.

## 1. cheap model + small outputs (the dominant factor)

Most calls run on **claude-haiku-4-5** at **$1 / $5 per million input/output
tokens** — the cheapest current model. The flows are extraction, labeling, and
synthesis: they read a moderate prompt and emit a *small* structured result, so
cost is overwhelmingly **input-side**, and input on haiku is cheap. A 7-day
window typically looks like:

- claude-haiku-4-5 — the bulk of calls, a few dollars
- claude-sonnet-4-6 — a few dozen docket-synthesis calls (`$3/$15` per Mtok), ~$1
- text-embedding-3-small — ~2k embedding calls, cents in total

Embeddings are the clearest case: thousands of calls, total cost in the cents,
because the per-call token count is tiny. The panel shows enough decimal places
to keep these from rendering as a misleading `$0.00`.

## 2. prompt caching (a real ~10% input discount, not a 10× one)

Anthropic prices the prompt side in three tiers:

| token kind | price vs. base input |
|---|---|
| uncached input | 1× |
| **cache read** (prefix served from cache) | **0.1×** |
| cache write (prefix written to cache) | 1.25× (5-minute TTL), 2× (1-hour) |

Every LLM flow makes many calls in a burst that share a byte-identical prefix —
the same system prompt, and for the tool-heavy `curate` agent the same
tool-definition block. The first call *writes* that prefix to cache (paying the
1.25×/2× premium once); the rest *read* it at 0.1× within the TTL. Break-even is
~2 reads (1.25× write + 0.1× read = 1.35× < 2× for two uncached prompts), and
our bursts comfortably clear that.

**But the realized cache-hit rate is modest — typically ~10–15% of prompt
tokens, not ~90%.** The cached prefix is the *constant* part (system prompt,
tool defs); the *volatile* per-call input (conversation histories fed to
`compact`, cluster contents fed to the labelers and `docket`) is large by
comparison and is never cacheable. So caching shaves roughly a tenth off the
input bill — genuinely worth having, free to enable, but not the reason the
total is small. The panel surfaces the realized rate
(`cache_read_tokens / (input + cache_read + cache_write)`) so this stays honest:
if it reads ~11%, that's expected, not a sign the cache is broken.

To move that number up you'd shrink the volatile portion (or cache more of it),
not add more breakpoints to the already-cached prefix.

## where caching is configured

We don't hand-place `cache_control` breakpoints — pydantic-ai's
`AnthropicModelSettings` do it. Each LLM flow opts in where the leverage is:

| flow (`flows/…`) | model | caches | TTL | why |
|---|---|---|---|---|
| `brief.py` | claude-haiku-4-5 | instructions | 5m | constant `SYSTEM_PROMPT`; ≥2 `agent.run()` per run, or two runs inside the window |
| `compact.py` | claude-haiku-4-5 | instructions | 5m | relationship-summary + likes-extraction bursts share one system prompt |
| `phi_atlas.py` | claude-haiku-4-5 | instructions | 5m | ~110 cluster-label calls/run, identical one-line prompt; marginal $ but free |
| `docket.py` | claude-sonnet-4-6 | instructions | 5m | one synth call per qualifying cluster; ~1.5KB prompt reused across the burst |
| `curate.py` | claude-haiku-4-5 | instructions **+ tool definitions** | 5m | tool-heavy multi-turn agent (10+ tools); every turn re-sends both blocks, so every turn after the first is a cache hit |

The sibling **phi** bot (`zzstoatzz.io/bot`, the `phi` row in the panel)
caches its ~30 tool definitions (~12k tokens) at a **1-hour** TTL via
`anthropic_cache_tool_definitions="1h"` — the longer window covers notification
bursts and tool-call loops across a single active period (it intentionally does
*not* bridge the 4-hour cycle cadence). Its system prompt isn't cached yet
because dynamic per-run context (notifications, episodic recall, per-author
memory) is injected into the system prompt and would invalidate any prefix
cache every run; a planned refactor moves that state into the user message to
unlock system-prompt caching.

### the prefix invariant (why the cache works at all)

Caching is a **prefix match**: any byte change anywhere in the cached prefix
invalidates everything after it. Render order is `tools` → `system` →
`messages`, so caching instructions covers tools too when tools come first. The
rule: keep the system prompt and tool list **stable** (cached), and put
everything that varies between calls — the question, retrieved rows, timestamps,
IDs — *after* the breakpoint. The classic silent cache-killers (a
`datetime.now()` in the system prompt, unsorted `json.dumps`, a per-user tool
set, swapping models mid-run) all live on the volatile side here, which is what
keeps even the modest read rate from collapsing to zero.

## 3. measurement (so none of the above is a guess)

`packages/mps/src/mps/spend.py` records `cache_read_tokens`,
`cache_write_tokens`, `provider`, `model`, and the per-tier costs on every call
(pulled from pydantic-ai's usage object). The hub reads the live
`llm-spend.jsonl` and the cost panel renders the per-window model breakdown and
the realized cache-hit rate. If reads ever drop to zero across a window where
you'd expect hits, a prefix invalidator has crept in — diff the rendered prompt
bytes between two calls of the same flow.

Pricing tiers and prefix mechanics are Anthropic's; see
<https://platform.claude.com/docs/en/build-with-claude/prompt-caching>.

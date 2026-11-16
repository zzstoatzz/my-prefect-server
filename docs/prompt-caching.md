# prompt caching and spend

A configured cache is not evidence of a cache hit. Inspect recorded input,
output, cache-read and cache-write tokens for the same model, flow revision and
measurement window before explaining a low cost or changing prompts.

The shared [spend helpers](../packages/mps/src/mps/spend.py) record usage and the
pricing basis. Missing prices remain unknown. Provider invoices, subscription
allocation and local token estimates answer different questions; see
[coding-job evidence](coding-jobs.md) and [cost coverage](costs.md).

## configuration and measurement

Pydantic AI model settings select caching where supported. Inspect the agent's
actual settings and selected model in the flow or shared helper; the configured
model can differ from an older deployed wheel. The briefing helper also has an
application-level result cache, which can avoid an inference call entirely.
That is separate from the provider's prompt cache.

Cache eligibility depends on the provider/model and the repeated prefix, not
just total request size. Check the current provider documentation before tuning
minimum sizes or TTLs. Do not pad prompts merely to cross a caching threshold.
Measure actual usage and latency before and after a change with comparable work.

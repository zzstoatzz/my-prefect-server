# Gardener investigation, September 11

Real task: investigate the abbreviated deployment identifier Phi used during an
operator DM. Prefect run `5cf73968-3c99-4293-a4a8-74baf884ee1b` completed on
September 11 at 15:56:52 UTC. Gardener inspected the bot source with Pi's
read-only tools in a Sprite and returned an artifact under `pi-agent-output`.

Gardener found the eight-character fallback in workflow context, plus omitted
run IDs. Alert reporting and private conversation context preserved their
identifiers. Both findings were reviewed against source; bot commits `ca9a576`
and `2969aeb` preserve deployment and run IDs. The installed Fly source was
inspected after deployment and contains both changes.

The inference grant for attempt
`prefect-ed3e4f65-5cf739683c994293a4a874baf884ee1b-r0` records model
`openai/gpt-5.6-luna`, 11 admitted requests, a limit of 32, and revocation after
completion. This proves admission and lifecycle enforcement for this attempt;
it is not token usage, cost, or proof every admitted request succeeded.

## Next diagnostic improvement

The bridge owns upstream authentication, restricts the endpoint/model, clamps
output tokens and rejects caller headers and redirects. Its current durable
record counts admitted requests, but does not retain request outcomes or
duration. Add metadata-only request events associated with the trusted grant's
attempt: admission/denial, outcome, duration and model. Never record bearer
tokens, prompts, response bodies or raw upstream errors. Invalid credentials
cannot be assigned a trusted attempt.

Keep Prefect/tool evidence alongside inference evidence. Aperture HTTP connector
proxy calls are not included in its request captures, so routing a diagnostic
tool through Aperture would not by itself provide the missing tool trace.

No broader model allowance or tool access was needed for this investigation.
The existing model-routing audit remains relevant when another model is
deliberately enabled; this run does not justify increasing the model budget.

## Deployed verification

Commit `d7ca887` adds metadata-only inference outcome logging and was deployed
to the trusted inference service on September 11. Follow-up Prefect run
`a0cf02c7-7242-4eca-b3cb-8e4edd332125` completed at 16:15:42 UTC and reviewed
the shipped identifier rendering. Its three requests appear in the service
journal under attempt `prefect-ed3e4f65-a0cf02c772424ecab3cb8e4edd332125-r0`,
with model Luna, HTTP 200, successful delivery, and durations of 3134, 5507,
and 8395 milliseconds. The grant records three of 32 requests used and is
revoked. Local tests also verify denied requests and credential redaction.

These are bridge request outcomes, not token billing or a replacement for
Pi's tool history. The follow-up artifact identifies the statements preserving
deployment and classified-run IDs; older runs remain available via detail
queries rather than being copied into the brief workflow-health block.

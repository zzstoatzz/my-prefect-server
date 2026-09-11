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

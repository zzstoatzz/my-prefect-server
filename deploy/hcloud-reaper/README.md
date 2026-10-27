# Builder expiry cleanup

This Worker runs every five minutes independently of HeavyPad. It deletes only
expired `owner=my-prefect-server,purpose=ephemeral-builder` resources whose
name, flow ID and attempt labels agree. The per-run expiry includes a 15-minute
margin beyond the configured job timeout. Live jobs are untouched. No HTTP
request can invoke cleanup; the fetch handler returns 404.

Before deleting an expired VM it checks the assigned primary IP and enables
provider-side IP deletion. Expired per-attempt SSH keys are removed only after
no server with that name exists. A later invocation completes any asynchronous
cleanup. API failures fail the invocation for observability; no credentials or
response bodies are logged.

A real provider test removed an expired attempt-zero VM, its key and IP, then
ran again with no changes. The original production node remained running. The
Worker build/dry-run passed. The same handler is exported as `reap(env)` for
real API qualification outside the scheduler; deployment must additionally be
verified by observing an actual scheduled invocation.

Install with `npm ci --ignore-scripts`; check with `cf deploy --dry-run`. After
committing/pushing the source, run `uv run python scripts/deploy_hcloud_reaper.py`
from the repo root. This reads the existing canonical project token, supplies
it through a private temporary file, and removes that file on return. No
credential is embedded in configuration, source, argv, or the builder VM.
Consumer rotation is rerunning this deployment after canonical token update;
issuer revocation/creation remains a Hetzner console operation.

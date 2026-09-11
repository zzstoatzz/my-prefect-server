# September 10 flow failure follow-up

Inspected production Prefect records, Heavypad's systemd journal, deployment
configuration, the deployed Studio Git revision, and public Tangled key records.
The MCP returned `All connection attempts failed`; the authenticated API worked.

## Recovered and experimental runs

- `watch-fastmcp` run `9a27cd20-b121-4c7e-8126-490f9e768654`: Heavypad's
  journal at September 9 23:05 UTC contains the original failure: HTTP 504 while
  `_workspace_resolver.prepare_workspace_for_flow_run` called `read_flow_run`.
  Execution never reached the flow. The missing workspace manifest was a
  secondary crash-hook failure. Later runs completed. The journal search found
  one matching 504 from 22:00 to 08:00 UTC; this establishes the immediate
  cause, not why the upstream request timed out.
- `autofix-revise` run `ea2d7c94-f749-43d5-a851-e7f6b5e60b52` ran on
  Heavypad and could not import `flows`; crash-hook loading also lacked `mps`.
  The deployment now targets `phi-sprites-spike`, where run
  `5ab746b5-12a0-4deb-a65f-33c06cdea730` reached Revised.
- Sprites boundary run `c45aadca-9792-499c-aeff-6b11a78ac5a8` failed, but
  the same deployment's rerun `8203206b-2f5a-439b-b2f6-4f83f9cdd00b`
  completed at September 10 02:56 UTC. The temporary deployment was subsequently
  removed. Do not classify the earlier failure alone as an ongoing outage.
- Sprites supervisor run `26367a6b-34a9-4e0e-ac16-8ee399e837fc` was an
  intentional fault injection. Its launch script explicitly sends SIGKILL to
  its parent; the corresponding request artifact identifies this run. Reporting
  a runtime restart here is expected test evidence.
- The earlier isolated-Pi permission failure and generic startup failure are
  experimental failures. Later successful worker/revision runs demonstrate
  working execution paths, but do not independently reproduce every earlier
  failure. The exact causes of those two historical errors remain unverified.

## Merge authentication

Run `efa3f625-9fc1-4ffa-bad8-b4755c422a69` failed during push after approval.
The current `tangled-merge-ssh-key` derives fingerprint
`SHA256:mcTROVP759pqOKkgrRwcTsuFuBhG8sfGv5oycADLUH4`. It matches none of
the six public keys registered in `zzstoatzz.io`'s `sh.tangled.publicKey`
collection, including all pages. No private key was printed or retained in a
temporary artifact. The flow already uses `IdentitiesOnly=yes`.

`knot_head` calls `git ls-remote` on a public repository. This checks reading
refs, not push authorization, despite its docstring. Registering a key or
changing the merge credential requires an explicit decision about the intended
identity and permission scope; neither was changed during this investigation.

## Studio and Phi

Studio run `f77f053e-65d3-4b7c-88d4-650432eb016c` used revision
`e41cc60dd4464129977c2af8ecb03f2100f857b8`. Gemini reported
`GenerateRequestsPerDayPerProjectPerModel-FreeTier=20`. This revision already
extracts quota evidence and disables immediate retries for daily exhaustion.
The community flow nevertheless records the session failed and raises; it has
no deferred-quota outcome. Changing retry delays will not resolve the daily
allowance. No generation, publication, billing, or credential changes were made.

Phi's editorial and chicken-scout runs received HTTP 409. Earlier inspection
found the deployed control endpoint's voice-reset guard returns 409; the
historical response body was not retained, so that remains the supported
explanation rather than proof from a captured response. Phi's live health check
is healthy and chicken-precheck subsequently completed. Future errors should
retain a bounded safe reason so intentional resets are distinguishable from
other conflicts. No trigger was replayed.

## First local repair: autofix metadata

`gather` previously abandoned all evidence if a referenced deployment had been
deleted. `checkout_as_of` received None for startup crashes and failed before
diagnosis. The local patch catches only deployment ObjectNotFound, retains run
logs and task evidence, and selects start time, state timestamp, then creation
time. Other API errors still propagate to the existing Degraded outcome.
The prompt now describes the inferred checkout honestly; resolving exact pins
and other repositories remains separate work.

Regression tests cover deleted deployments, propagation of other API errors,
and each timestamp fallback. The existing credential screening remains intact.
At initial triage this patch was local and had not emitted alerts. Nate
subsequently authorized shipping it; deployment verification uses dry-run mode
so it does not emit diagnosis events or publish a proposal.

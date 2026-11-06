# DST maintenance server rollout

HeavyPad API and services run server main
`e0da8de3e355437cc6fcb585fc42c8f76af49a39`, verified November 6, 2026 at
19:13 UTC. The [receipt](2026-11-06-dst-maintenance-rollout.json) contains exact
image pins, deployment generations and canary states.

The release fixes memory lease reads fabricating missing resource IDs on
allocation failure, updates zat/zttp/WebSocket, and repairs optional DST tooling.
The application runtime remains Threaded; zio is an optional compatibility test.
Server pipeline `3mzo34rv4bs22` passed both Linux CI and image publication for
this exact main revision, including the database/broker and real-worker checks.

The registry-confirmed image index is
`sha256:16f04ddffe1d52af38fe2b2370aebaaf55298cb59f3626b5af6994a122fc006c`;
its Linux amd64 manifest is
`sha256:a36f50714be191838de42478fb2866fdd4999b7998bb34d94d76d811e096fbf0`.
Configuration commit `29e58dfd065956b3037da70c25b3b91eeedeab41` was pushed to
GitHub main and Tangled main before applying. The reviewed live diff changed
only API/services image references and their generated deployment generations.
The API document was applied first through `just kubectl`, followed by services
after API readiness and public source-version readback succeeded.

API generation 8 and services generation 7 each reached one updated, ready
replica. Both new pods had zero restarts. The final targeted manifest diff is
empty. UI, resource settings, credentials, schedules and worker configuration
were unchanged. Postgres and Redis retained their pre-rollout pods; the recent
Redis resource change was preserved.

The existing manual-only `prefect-home-canary/canary` deployment completed once
before rollout and once after rollout, each in one attempt. Post-rollout run
`0bc3bd0c-4e14-455d-ad4b-7132077d4d00` completed at 19:13:38.901051 UTC.
Public `/api/version` returned `0.0.31+e0da8de3e355437cc6fcb585fc42c8f76af49a39`.

Rollback restores the per-component prior image pins recorded in the receipt
through `stack.yaml` on main, then applies only the API/services documents using
the targeted CLI. Source investigation and release preparation used a separate
checkout to preserve existing unfinished Typeahead edits in the primary checkout.

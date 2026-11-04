# Stream activity watch

`watch-stream/watch-stream` runs every five minutes on HeavyPad. It reads
stream's public `/metrics`, plus relay-eval's `/api/rate-profile` and
`/api/status`; stream itself has no alerting or webhook responsibility.

The rate is upstream sequence advancement between observations, compared with
the same hourly medians the stats page uses (weighted across hour boundaries).
Two consecutive clean elevated windows, spanning at least nine minutes, at
2x the median open an episode. Two windows below 1.5x close it and re-arm.
An episode receives only one wake, even across deployments.

A usable interval is 4–15 minutes, with no process restart or reconnect,
nondecreasing positive sequence, and upstream event timestamps within 60 seconds
of both observations. Relay-eval's latest checks for stream.waow.tech and
bsky.network must be within 75 minutes, connected, not behind, and at least
95% coverage. Other relays cannot veto detection. These checks reduce replay
artifacts; they do not prove unique activity or its cause. Missing/malformed
inputs fail the run, retaining its previous state. Unusable intervals break
confirmation without clearing an already-open episode.

The Prefect Variable `stream-activity-watch` stores the previous observation,
confirmation times, active event ID, and pending delivery. Deployment concurrency
is one. Pending delivery is persisted before sending, retried with its stable
ID, and cleared only after Phi accepts it. Phi uses the existing
`phi-control-token` Secret block and `/api/control/trigger/network-activity`.
The receiver persists its most recently accepted ID to suppress retries.
Acceptance means a background investigation was scheduled, not that research
completed; process failure after acceptance can still lose that investigation.

Phi receives the measured rates, observation times, relay checks and source
links, with cause explicitly unknown. The pass is separate from operational
alert escalation. No public post is required. Pause/voice reset refuse new
delivery so it remains pending; operator override still gates agent actions.

To inspect or run the production deployment:

```sh
just prefect deployment inspect watch-stream/watch-stream -o json
just prefect deployment run watch-stream/watch-stream --watch
```

Do not delete the state Variable as routine deployment work: it resets episode
suppression. No account credentials or new secrets are introduced.

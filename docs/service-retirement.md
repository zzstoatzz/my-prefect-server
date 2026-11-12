# service retirement

Use this procedure before removing a deployed service, deployment, or data consumed
outside the code being changed. A failed job or deleted host does not establish
that its consumers have stopped using it.

1. Establish current routing and runtime ownership. Check the home repository's
   `deploy/cloudflared.yml`, this repo's `deploy/prefect-edge/` configuration,
   HeavyPad user services, and containers on its system Docker socket.
2. Search consumer repositories for the hostname, endpoint or data path. Check
   access logs as well; absence from one source is not proof of no consumers.
3. Check and update [analytics ownership](analytics.md) for affected tables.
4. Preserve recoverable data and configuration; record what was moved.
5. Obtain Nate's approval before removal if any consumer remains.

For `hub.waow.tech`, explicitly check the discovery-pool API used by Phi, cost
exports, presence webhook/API, and the page. Confirm their current owners and
usage rather than treating this list as exhaustive.

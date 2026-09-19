# ts-unplug beside isolated Pi — September 18, 2026

The live proof passed. Pi inside the existing sandbox reached Aperture through
an independent ts-unplug node, without Funnel or the remote inference grant
service. No production deployments, worker services, or tailnet policies changed.

## Tested path

Pi → namespace loopback relay → existing Unix-socket inference filter →
ts-unplug on the Sprite's localhost → Aperture → Claude Haiku 4.5.

The user approved the test node interactively in their normal browser. Tailscale's
Machines page showed `phi-tsunplug-proof-20260918`, connected and **Ephemeral**,
under the user's existing identity. This proves that identity's existing access;
it does not prove a deployment-specific least-privilege tag policy or per-run
Aperture billing attribution. No broader permissions were added.

`tests/sprites_tsunplug_probe.py` returned:

```json
{"live_aperture": true, "model": "anthropic/claude-haiku-4.5", "pi_response": "tsunplug-proof-ok", "boundary_passed": true, "forbidden_routes_rejected": true, "wrong_model_rejected": true, "remote_grant_service_used": false}
```

Before the live request, the same real Pi executable passed against a fake SSE
upstream. Both runs used the production sandbox command and inference filter.
The checks established UID 2000, no supplementary groups or effective
capabilities, no-new-privileges, hidden supervisor paths/environment, no general
network routes, no direct connection to the outer localhost proxy, writable
workspace, read-only Git metadata, and rejection of an unauthorized model and
the workflow-request route. A symlink could not expose a supervisor canary.

## Findings that affect migration

1. **Tailscale authentication and provider authentication are separate.**
   The live gateway advertises subscription routes with `requires_client_auth`.
   The old `openai/gpt-5.6-luna` identifier returned a 404 for the test identity;
   `/v1/models` advertises `gpt-5.6-luna` instead. The proof therefore used the
   existing supported Haiku mapping and the existing Anthropic credential.
2. That credential was decrypted locally into memory and delivered over captured
   Sprite stdin to the trusted probe process. It was injected by the existing
   inference filter, never included in Pi's environment/configuration, command
   arguments, or a persisted credential file. This moves provider-credential
   handling onto the Sprite supervisor; it is a substantive change from keeping
   it exclusively on Heavypad. A production migration needs to accept that trust
   boundary or arrange gateway-side credential injection.
3. ts-unplug replaces authenticated network transport, not the local API/model/
   request filter. The proof retained that filter and a three-request/64-output-
   token limit. Wrong-path and wrong-model rejection were exercised; request
   exhaustion was not separately exercised in this probe.
4. The current inference service also serves Phi workflow requests. Replacing
   inference transport alone does not permit deleting that service.
5. Keep test Sprites inference-free. Their package-install sandbox shares the
   Sprite network namespace and could otherwise reach a localhost proxy.

## Reproduction and provenance

- Disposable Sprite: `phi-tsunplug-proof-20260918`, organization `nate-nowack`.
- Official `tailscale/ts-plug` source:
  `0b0b083dae1aad0ad55c62ba28d3402f421a9aab`.
- Go 1.26.0, Linux amd64, `CGO_ENABLED=0`. The local default Go 1.27 failed
  against the pinned experimental JSON dependency.
- Stock binary SHA256:
  `73c6412bf5b757790b0ea96c5718283ec19bdbe73f9a706624e0eae3857216d9`.
- The live binary has exactly one source change: `Ephemeral: true` in its
  `tsnet.Server` initializer. SHA256:
  `6fc5551faee805464e8a34e87f732eb3977737781d5617d5f5a63865a011abb2`.
  It is a modified proof build, not a stock CLI ephemeral flag.
- Node 24.18.0, Pi 0.84.4, bubblewrap, socat, and Prefect 3.7.7.
- Proxy: `ts-unplug-ephemeral -dir /var/lib/tsunplug-proof -hostname phi-tsunplug-proof-20260918 -port 18080 ai.tailb660b6.ts.net`.
- Probe: `python tests/sprites_tsunplug_probe.py --live --model anthropic/claude-haiku-4.5 --credential-stdin`, with mps on PYTHONPATH.
  The stdin JSON field is `anthropic_api_key`; never pass a real value in arguments.
- Without `--live`, the probe uses fake inference and needs no provider credential.

Automatic noninteractive enrollment remains untested. A production worker needs
an enrollment mechanism, restart-safe node identity, explicit cancellation/logout,
and cleanup recovery after worker death. Per-run model scoping remains enforced
locally until an equivalent gateway policy is verified.

## Cleanup

Cleanup completed: explicit tsnet LocalClient logout removed the ephemeral node.
The Tailscale Machines page returned from six to the original five machines,
with the test node absent. The disposable Sprite was destroyed and the Sprites
list no longer contained it. This verifies explicit cleanup, not Tailscale's
passive offline-garbage-collection delay. The private enrollment log was removed.

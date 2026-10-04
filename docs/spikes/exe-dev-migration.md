# moving gardener's Sprite sandboxing to exe.dev — assessment, 2026-10-03

Read-only assessment. No code changed, no exe.dev account touched. exe.dev
claims below come from its public docs (`https://exe.dev/docs/all.md`, fetched
today); nothing was run against the service. "Not documented" means I searched
that file and found nothing, not that the feature is absent.

## verdict

Feasible on public features, and it is a provider swap, not a rewrite. The
Sprite-specific surface is `packages/prefect-sprites/src/prefect_sprites/provider.py`
plus the client calls in `worker.py`. The pieces that make this a sandbox
(bwrap boundary, inference grants, bridge) are ours and exe.dev does not
replace them. The real win is a baked image instead of per-run bootstrap.
Three things need answers before committing; two can be settled with probes
we already have, one needs nate's contact.

## what exists today

| layer | where | what it does |
| --- | --- | --- |
| worker | `prefect_sprites/worker.py`, run by `mps/sprite_worker.py` on heavypad (`deploy/phi-inference/pi-sprites-worker.service`) | one Sprite per flow-run attempt, named `prefect-<pool hash>-<run id>-r<n>`, labelled with pool + run for ownership. submit returns immediately; a 10s observer loop reconciles, crashes orphaned runs, stores a diagnostic artifact, destroys the Sprite |
| bootstrap | `provider.py` `INSTALL` | payload over exec **stdin** (never URL params): pinned uv 0.12.12, Python 3.13.7, requirements, local wheels, root-only `/var/lib/prefect-sprites` |
| supervisor | `prefect_sprites/runtime.py`, started as a Sprite service | runs the Prefect engine in a cgroup v2 group, timeout, SIGTERM cancel, `cgroup.kill` cleanup, atomic `state.json` + log tail, and a 5-minute **keep-awake lease** on `/.sprite/api.sock` refreshed each minute |
| agent boundary | `mps/pi_sandbox.py`, `mps/pi_execution.py` | Pi runs under bwrap as uid 2000: no network namespace route, cleared env, no caps, read-only tools and `.git`, writable checkout + home only. installs node 24.18.0, Pi 0.84.4, bubblewrap, socat on every fresh Sprite |
| inference | `mps/inference_bridge.py`, `mps/pi_relay.py`, `mps/inference_grants.py` | Pi → loopback socat → unix socket → root bridge → heavypad Funnel `:8443` → Aperture. worker issues a per-attempt grant: one model, 32 requests, expiry, revoked on exit. upstream keys never leave heavypad |
| publishing | flows (`pi_pr.py`, `autofix.py`) | gardener PDS credentials are used by the trusted flow, outside the Pi boundary |

Pool `phi-sprites-spike` currently carries `autofix-revise` and
`test-pull-patch` (`prefect.yaml:1030`, `:1076`). `autofix` and `pi-pr` are
still pinned to the home pool. I did not check live worker or pool state.

## mapping to exe.dev

| need | exe.dev (public docs) | fit |
| --- | --- | --- |
| VM per attempt, create/delete by API | `new --sandbox --name=… --tag=…`, `rm`; standalone VMs billed hourly ($0.105 per 2 vCPU-hour), creation "about two seconds" | yes |
| control plane | SSH (`ssh exe.dev …`, `--json`) or `POST https://exe.dev/exec` with scoped, expiring tokens | yes, no Python SDK. this also retires the pinned `sprites-py` git revision |
| private stdin payload, long exec | `/exec` has **no stdin, 30s timeout, 64KB body**. plain `ssh <vm>.exe.xyz` has none of those limits | use SSH for bootstrap and inspection; `/exec` only for `new`/`rm`/`ls` |
| ownership + rediscovery | name prefix + tags, `ls --json` | likely. whether `ls --json` returns tags and creation time is not documented |
| supervisor as a service | no service API; VMs are ordinary Linux with systemd (their runner guide uses `systemctl`) | `runtime.py` survives nearly intact under a systemd unit |
| keep-awake lease | VMs are described as persistent, not serverless; no idle stop is documented | lease code can go, pending confirmation |
| root, cgroup v2 kill, root-created namespaces for bwrap | root via sudo. kernel is theirs ("you don't get to choose which kernel"); cgroup/namespace behaviour not documented | must probe |
| per-run bootstrap cost | custom Docker image (`new --image`), private registries, setup scripts, `cp` | better than today: bake uv, Python, node, Pi, bwrap, socat once |
| agent has no network | no egress controls documented | unchanged: bwrap `--unshare-net` stays ours |
| scoped inference credential | integrations inject secrets at the network edge, attachable per VM or per tag; LLM integration takes own API key or a custom HTTPS endpoint | optional later step. no per-VM request cap, model pin or expiry is documented, so it does not replace grants as-is |
| worker reaches VM without inbound exposure | no public IP, no private network between VMs; SSH via exe.dev | fine, worker only dials out |

Two defaults to turn off for gardener VMs: the `llm` and `reflection`
integrations attach to every VM (`auto:all`), and the default `exeuntu` image
runs the Shelley agent and ships its own `pi`. A custom image plus tag-scoped
attachments avoids both.

## may depend on non-public features — ask the contact

1. **Token blast radius.** Token `cmds` scope command names only. A worker
   token allowed `new`, `rm`, `ssh` can delete or enter any VM on the account.
   Sprites isolates this with a separate org. Is there resource- or tag-scoped
   authorization, or is a separate account the answer?
2. **Per-VM limits on integrations**: request caps, budgets, expiry, model
   restriction. Without them the heavypad grant store stays.
3. **Egress policy per VM.** Not needed for parity, but it would let us drop
   reliance on bwrap alone for network isolation.
4. **Create/delete churn**: rate limits are per SSH key with no published
   numbers; Personal allows 50 VMs.
5. **Kernel guarantees**: cgroup v2 delegation and namespace creation staying
   available, given the hypervisor "may change".
6. **Per-VM usage or cost export** for Evergreen; `stat` and `billing usage`
   exist, per-VM cost attribution is not documented.

## live probe, 2026-10-04

One VM, `gardener-probe` (default `exeuntu` image, lax, 2 vCPU / 8 GB), on a
new account `n8@zzstoatzz.io`. Created, probed, deleted; `ls --json` is empty
again. One VM is one sample: it shows these things work, not that they stay
working.

- **Kernel and tools.** Linux 6.12.93, Ubuntu 24.04.5, systemd as PID 1,
  passwordless root, cgroup v2 with `cgroup.kill`. Already on the image:
  uv 0.12.20, bubblewrap 0.9.0, socat, setpriv, pi 0.87.1, docker, tailscale.
  Not on it: node on PATH, our pinned Pi 0.84.4.
- **`tests/sprites_lifecycle_probe.py`: passed, unchanged.** Detached-child
  and supervisor-death cases both ended with an empty cgroup.
- **`tests/sprites_agent_boundary_probe.py`: passed, unchanged.** uid 2000,
  no caps, no routes, host files hidden, checkout and home writable.
- **`tests/sprites_bridge_probe.py`: passed, unchanged**, both the curl leg
  and the real Pi 0.84.4 leg through the relay and unix socket. The first run
  timed out because my ssh invocation left stdin open and Pi waited on it;
  with stdin closed it passed. That was my harness, not the platform.
- **Toolchain install** on the fresh VM: node 24.18.0 download and unpack 2s,
  `npm install` of Pi 4s.
- **Reachability from the VM:** heavypad inference endpoint returned 401
  unauthenticated (as designed), `prefect-server.waow.tech/api/health` 200.
- **`ls --json`** returns `tags`, `created_at`, `status` and `vm_name`, which
  is what the observer needs for ownership and rediscovery.

Findings that change the plan:

- **The default `llm` integration is reachable with no key from any VM**
  (`https://llm.int.exe.xyz/v1/models` returned 200), and on this account it
  is backed by nate's ChatGPT subscription. Pi cannot reach it from the
  no-network namespace, but the root flow engine can. Gardener VMs should not
  have it attached; `reflection` likewise exposes the owner email.
- **Plan.** The account reports `"plan":"Basic"`, `"paid":false`,
  `"max_vms":0`, yet `new` worked once the ChatGPT subscription was connected.
  `new --sandbox` is refused on this plan. So per-run VMs here would be
  ordinary VMs, and the real VM limit on this plan is unknown.

## migration sketch

1. Answer question 1 first; it decides account layout.
2. Done 2026-10-04, see above: the three existing probes pass unchanged.
3. Build one image with the pinned toolchain; confirm creation time with it.
   Detach `llm` and `reflection` from `auto:all`, or scope them by tag.
4. Built 2026-10-04, not yet deployed: `packages/prefect-exe` (worker type
   `exe`) and `mps.exe_worker`, which reuses the heavypad grant callbacks.
   The control plane is plain `ssh` with exe.dev's host key pinned; the
   supervisor is the Sprites one minus the lease, run as a systemd unit.
   `tests/exe_provider_probe.py` passed against two real VMs (create 3-5s,
   bootstrap 3-6s, cancel 0.6s, outcome survives a supervisor restart).
   Prefect run `8f1db1d7-4b12-4ca3-920c-5cc9b9c00640` then reached Completed
   on pool `gardener-exe` from a worker on the laptop: create 1.9s, bootstrap
   4.6s, service start 7.6s, flow logged `exe-worker-ok uid=0`, artifact
   stored, VM deleted. One run. Not yet exercised: the worker on heavypad
   with inference grants, and Pi inside a run.
5. Move `test-pull-patch` to the new pool, then `autofix-revise`. Sprites pool
   stays until both have run clean across more than one attempt each.
6. Only then consider swapping the bridge for an exe.dev integration, if
   question 2 comes back favourably.

Smaller than the original Sprites worker build; larger than a config change.
Blocked on question 1 and on which plan gardener runs under.

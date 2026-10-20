# Bandwidth watchdog

A systemd user service on heavypad samples Linux TCP socket counters every five
seconds. It emits `bandwidth.*` Prefect events and sends warning evidence to
Logfire (`zig-prefect-server`, service `bandwidth-watchdog`). Prefect sends the
operator a deterministic Discord notification. A Logfire alert uses the existing
`phi alert watch (push)` channel; Phi needs no new code or polling loop.

## Policy

Decimal GB throughout. Defaults in `config.json`:

- 5 GB observed Internet TCP in 15 minutes: warning, at most once per hour.
- 5 GB physical-interface traffic in 15 minutes: separate conservative warning,
  explicitly including LAN and all protocols.
- 20 GB observed Internet TCP per registered download service in an hour: freeze
  its entire systemd cgroup, retaining files and process state. Only
  `deepseek-v41-download.service` is initially registered. Other services are
  observed and reported, never frozen.
- 100 GB observed TCP during the UTC calendar month: warning, not a claim about
  remaining ISP allowance. This deliberately catches large consumption well
  below the user's approximately 1 TB household plan.
  Monthly messages lead with the observed total and configured warning threshold,
  UTC month, and monitoring start time; recent usage follows separately.
  Notify once per threshold multiple (100, 200, 300 GB by default), with the
  existing one-hour cooldown between milestones. Quiet hours do not repeat the
  same warning. A jump across several milestones produces one notification;
  pending milestones are reported after cooldown. Messages name the next milestone.
  The recorded milestone survives restarts and resets with the UTC month.
  Upgrading an existing state preserves accounting and remembers its first monthly
  warning, so deployment does not repeat it. Spike alerts retain their own cooldowns.
- A heartbeat every minute; a Prefect automation alerts after five minutes missing.

The local sampler and freezer do not need Prefect or Logfire to be reachable.
Events persist in an atomic state file and are retried with their original UUID.
Pending heartbeats coalesce. A file lock prevents concurrent samplers. systemd
restarts failed samplers. The unit freezes with systemd's cgroup freezer; pausing
only the parent process would leave its download children consuming bandwidth.

## Measurement limits

Observed Internet TCP is a **lower bound**. Connections that open and close
between samples, UDP/QUIC, VPN encapsulation and traffic in other network
namespaces are not attributed. Private, loopback, link-local, multicast and
Tailscale/CGNAT peers are excluded. A TCP connection to a public peer through a
VPN can be counted even when it is actually a local proxy; this is not ISP billing.
Physical-interface counters provide a separate conservative backstop and include
LAN traffic. The monitor does not observe other household devices.

The first sample establishes a baseline. It does not put existing connection
lifetime totals into the current 15-minute window. Restarts preserve samples and
monthly observed totals; boot/counter resets never add negative bytes. Sampling gaps over 30 seconds
establish a fresh baseline rather than assigning old traffic to a short window. All monthly
figures start at installation and are not historical billing totals. The already
paused model download remains paused and is not silently counted as new usage.

## Install and operate

Use `just deploy-bandwidth-watchdog` after committing and pushing the source.
The release is a small standalone copy of `mps.bandwidth_watchdog` with a locked,
offline-installed httpx/logfire environment, not the full application dependency
set. It reuses the existing prod-worker Prefect environment and reads the existing
`logfire-server-write-token` Secret block without printing it or writing another
credential copy. No firewall changes or root privileges are required.

Apply only the two named automations with:

```sh
just automations --name 'bandwidth warning -> discord' --name 'bandwidth watchdog missing -> discord'
ssh heavypad 'systemctl --user status bandwidth-watchdog'
ssh heavypad 'journalctl --user -u bandwidth-watchdog -n 30 --no-pager'
ssh heavypad 'jq "{summary, paused, pending_events: (.outbox | length)}" ~/.local/state/bandwidth-watchdog/state.json'
```

Edit `~/.config/bandwidth-watchdog/config.json` and restart the service to change
thresholds or register another download unit. Unit names must be literal; no
wildcards. Put a download in its own `systemd-run --user --unit=NAME` service to
make the freeze boundary explicit. Do not register a shared worker service.

A freeze stays latched in `state.json` and requires operator review. Before
resuming, stop the watchdog, remove the unit from `pause_units` (explicitly opting
it out), restart the watchdog, then `systemctl --user thaw NAME`. A SIGSTOP-paused
process also needs SIGCONT; thaw alone does not undo the manual DeepSeek pause.
Do not delete the accounting state to resume work. Thawing a registered unit while its hourly budget is still exceeded makes the
watchdog freeze it again. The `paused` map records the last enforcement; it does
not exempt future runs of that unit.

## Verification

`uv run pytest tests/test_bandwidth_watchdog.py` checks baseline/restarts, byte
windows, monthly reset, peer exclusion, durable HTTP retry and singleton locking.
`check_bandwidth_watchdog.py` on Linux transfers a few hundred KB over loopback,
checks the production parser excludes it, and freezes a disposable systemd unit
using lowered test-only thresholds. It stops that test unit in `finally`.
No large Internet download is required for verification.

Verified on heavypad: controlled loopback transfers crossed a 64 KiB test budget,
froze the disposable cgroup, did not duplicate enforcement while frozen, and
froze it again after an intentional thaw. Production peer filtering excluded
that traffic. Test event `dcb7856b-35fb-40e0-86b3-8ec0856b89c2` reached Prefect;
`bandwidth warning -> discord` recorded action execution
`2145f9b8-9794-43cc-8dc6-16a6d8a4693e`. Its labeled test evidence also arrived
in Logfire. A production service restart retained accounting state and left the
three manually paused DeepSeek processes stopped.

The Logfire scheduled evaluation `500b7c90-a966-4eaa-b697-d85c11227b02`
matched the test evidence and delivered it to Phi's existing channel at
2026-10-17 00:57:23 UTC. Phi returned HTTP 200 with `{"ok":true,"opened":true}`.
The full `just check` passed (717 tests, 3 existing skips, Svelte check and lint);
the final targeted watchdog suite passed all 15 tests.

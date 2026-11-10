# ycc (heavypad)

[ycc](https://github.com/whyrusleeping/ycc) is a coding harness built around a
long-running daemon: each project keeps a spec and a backlog, and the daemon
runs chat sessions and unattended work loops against them. This runs one daemon
on heavypad next to albedo (`deploy/albedo-phone/`), with DeepSeek through
OpenRouter, reachable from the tailnet at
`https://heavypad.tailb660b6.ts.net:12443`.

## what the agent can reach

ycc confines only its reviewers' shells; the coordinator and implementer run
shell commands as the daemon's user. So the whole daemon runs inside
`sandbox.sh`, a bwrap wrapper with the same shape as albedo's: `/nix`, `/usr`
and `/etc` read-only, `~/ycc/home` as the home directory and `~/ycc/work` for
projects. None of the real home is visible: no sops keys, no
`~/.config/prod-worker/env`, no other checkouts. `uv` is bound in so an agent
can run a Python project's tests, with `UV_OFFLINE=1`: the home connection is
metered and the first replay pulled 1.3G of packages into the sandbox's own
cache. A project whose dependencies are not in `~/ycc/home/.cache/uv` yet
fails to sync instead of downloading; an agent can unset the variable, so it
stops accidents and is not a network control. `ycc doctor` inside the
wrapper reports reviewer confinement via bwrap; a real review inside the
wrapper has not been exercised yet.

Two limits to know:

- Network is shared, because the model is remote. An agent can reach
  heavypad's loopback services and the tailnet. albedo has a hook that denies
  those (`~/.albedo/guard.py`); ycc has no equivalent here yet.
- The OpenRouter key sits in `~/ycc/home/.config/ycc/secrets.json`, inside the
  sandbox. ycc strips known credential variables from tool shells, but an
  agent's shell can read that file.

A project is a git clone under `~/ycc/work`. Give it no push credentials: the
agent commits locally and a human fetches from the clone.

## model

`ycc.toml` seeds `~/ycc/home/.config/ycc/ycc.toml` on first install. The
daemon writes settings changes back to that file, so later installs leave it
alone; edit it on heavypad or from the web client's settings.

- Every role is `deepseek/deepseek-v4.1-flash` over OpenRouter's
  chat-completions endpoint (`backend = "openai-compatible"`).
- ycc ignores thinking and effort settings on this backend and says so once
  per session.
- Prices and the context window are OpenRouter's catalog values on
  2026-11-09, in dollars per million tokens. ycc reports cost as unknown
  without them, and the `[budget]` dollar caps need them: a session stops at
  $2 and a work loop at $5.

## one-time setup

```sh
# from the laptop: build at the pinned commit, install the unit, store the key
just ycc-build
just ycc-install
just ycc-key
ssh heavypad systemctl --user restart ycc

# on heavypad, once: publish it on the tailnet
tailscale serve --bg --https=12443 http://127.0.0.1:4327
```

The daemon listens on `127.0.0.1:4327` (ycc's default 8787 is taken on
heavypad). `ycc-key` sends the key from the secrets store over ssh stdin into
`ycc token set` and never prints it. Rotating the key means re-running it and
restarting the unit.

To move to a newer ycc, change `YCC_REV` in `build.sh`, then `just ycc-build`
and restart the unit.

## signing in

The web client asks for the daemon's access token. The daemon generates it on
first start and keeps it at `~/ycc/home/.local/state/ycc/daemon-token`.
`just ycc-token` prints it; run that in your own terminal, since whoever holds
the token can run commands in every project the daemon serves.

## operating

```sh
ssh heavypad systemctl --user status ycc
ssh heavypad journalctl --user -u ycc -n 50
ssh heavypad '~/.local/share/ycc/sandbox.sh ycc doctor'
ssh heavypad '~/.local/share/ycc/cli.sh project list'
ssh heavypad '~/.local/share/ycc/cli.sh list'
tailscale serve --https=12443 off   # on heavypad: unpublish
```

`cli.sh` runs a ycc client command inside the sandbox against this daemon.
A bare `ycc` there does not find it: it probes ycc's default port, then runs
a throwaway daemon of its own.

Session logs and the project registry live under `~/ycc/home`. The daemon
runs inside the unit's cgroup, so restarting the unit restarts the daemon.

# albedo on the phone (heavypad)

`albedo web` on heavypad, reachable from any device on the tailnet at
`https://heavypad.tailb660b6.ts.net:11443`. albedo's web client is a single
chat at phone width, so this is the phone client; it drives the same daemon
the terminal client would, with DeepSeek through OpenRouter.

It reuses the drift audit's bwrap sandbox (`deploy/albedo-sandbox/sandbox.sh`)
in a sandbox dir of its own, `~/albedo-phone`: the audit stops its daemon after
every run, and a shared home would take the phone's sessions down with it. The
agent sees `~/albedo-phone/work` and nothing of the real home.

The server binds loopback only. `tailscale serve` terminates TLS with the
tailnet's certificate for `heavypad.tailb660b6.ts.net` and proxies to it,
tailnet-only. albedo's own `--tailnet-https` can't be used here: it refuses a
serve config that already publishes other things or uses Funnel, and heavypad
has both. `--public-url` is the manual equivalent: the printed link carries the
https origin, and the server accepts it as `Host` and `Origin`. The https origin
is what lets a phone browser use notifications and the clipboard.

## one-time setup

albedo itself is installed as in `deploy/albedo-sandbox/README.md`. Then:

```sh
# on heavypad, once (needs the sudo password): let stoat edit tailscale serve
sudo tailscale set --operator=stoat
tailscale serve --bg --https=11443 http://127.0.0.1:4317

# from the laptop: the OpenRouter key into the phone sandbox, then the unit
just albedo-phone-key
just albedo-phone-install
```

`albedo-phone-key` sends the key from the secrets store over ssh stdin and
never prints it; the daemon moves it into `creds.json` on its next start.
Rotating the key means re-running it and restarting the unit.

## signing a phone in

The server prints one sign-in link, and keeps it across restarts
(`~/albedo-phone/home/.albedo/web-key`). `just albedo-phone-link` prints it
(and draws it as a QR code when stdout is a terminal). Opening it once sets a
cookie; the link is the credential, so it stays on your own devices.
`just albedo-phone-link --new-link` replaces it and signs every browser out.

## operating

```sh
ssh heavypad systemctl --user status albedo-phone
ssh heavypad journalctl --user -u albedo-phone -n 50
tailscale serve status            # on heavypad: the 11443 entry
tailscale serve --https=11443 off # on heavypad: unpublish
```

The daemon runs inside the unit's cgroup, so a restart of the unit restarts
the daemon too; sessions live in the sandbox's SQLite and survive it.

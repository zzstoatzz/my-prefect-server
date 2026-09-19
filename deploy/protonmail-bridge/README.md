# proton mail bridge on heavypad

the `ingest` flow reads inbox mail over IMAP from Proton's official
[Bridge](https://proton.me/mail/bridge), running headless as the `stoat` user
on heavypad and listening on `127.0.0.1:1143` (IMAP) and `:1025` (SMTP),
localhost only. it replaced hydroxide on 2026-09-19 after Proton started
answering hydroxide's logins with a human-verification demand (error 9001)
that an unofficial client cannot complete.

## how it is set up

- the `.deb` from proton.me/download/bridge, installed with `apt install`.
- keychain: `pass`, backed by a passphrase-less gpg key generated for `stoat`
  (`~/.password-store`, key "proton bridge keychain"). the bridge records the
  choice in `~/.config/protonmail/bridge-v3/keychain.json` (`pass-app`).
  gnome-keyring is installed but locked without a login session, so the bridge
  falls back to `pass` on its own.
- account login was done once through `protonmail-bridge --cli` (`login`, then
  `info`), driven over a pseudo-terminal. Proton demanded human verification
  at that point and printed a verify.proton.me link; nate completed it in a
  browser. the CLI then printed the bridge's IMAP username and password, which
  went into the `proton-bridge-creds` Secret block (`{"username", "password"}`)
  and the sops store (`prefect/blocks/proton-bridge-creds`).
- this unit file lives at `~stoat/.config/systemd/user/protonmail-bridge.service`,
  enabled with `systemctl --user enable --now protonmail-bridge`. `stoat` has
  lingering enabled, so it runs without a login.

the bridge accepts a plain IMAP login on loopback (its `info` reports
STARTTLS as the configured security; `imaplib.IMAP4` without STARTTLS
authenticated fine on 2026-09-19).

## operate

```sh
sudo -u stoat XDG_RUNTIME_DIR=/run/user/$(id -u stoat) systemctl --user status protonmail-bridge
ls -t ~stoat/.local/share/protonmail/bridge-v3/logs/ | head -1   # newest log
```

the first sync after login walks the whole mailbox; the inbox fills in as it
goes. if Proton ever invalidates the session, `protonmail-bridge --cli` and
`login` again as above, then update the block and the store.

## privacy

email subjects + snippets flow into `hub_action_items` and `briefing.json`,
which hub.waow.tech serves; the hub must stay behind Cloudflare Access.

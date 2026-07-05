# hydroxide — proton IMAP bridge on heavypad

proton has no IMAP API; [hydroxide](https://github.com/emersion/hydroxide) bridges
it to IMAP on `127.0.0.1:1143` (localhost only, no TLS needed — never expose it).

the `ingest` flow's `fetch_emails` task reads from this bridge and writes
`raw_emails` to the analytics DuckDB; dbt scores them into `hub_action_items`
(source=`email`), so inbox items show up in the hub and the morning brief.

## setup (one-time)

```bash
scp -r deploy/hydroxide stoat@heavypad:
ssh stoat@heavypad 'cd hydroxide && chmod +x install.sh && ./install.sh'
# then follow the printed instructions: `hydroxide auth`, start the unit,
# and save the proton-bridge-creds Secret block.
```

auth state lives in `~/.config/hydroxide/` on heavypad. the flow skips email
(with a warning) when the block or bridge is missing, so ingest keeps working
for other sources until this is set up.

## privacy

email subjects + snippets flow into `hub_action_items` and `briefing.json`,
which hub.waow.tech serves — the hub must be behind Cloudflare Access before
email ingestion is enabled.

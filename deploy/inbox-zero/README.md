# Inbox Zero

A private review queue backed by SQLite. It combines Gmail and Proton inboxes,
prioritizes possible account alerts and direct correspondence, and groups mailing
lists and exact Message-ID duplicates. Rankings are transparent heuristics, not
verified urgency or instructions to act.

The existing daily email-triage flow continues unchanged. This service reuses its
Proton parsing helpers and adds durable review state and mailbox operations.

## Run

`uv sync --extra inbox`, then `uv run --extra inbox python deploy/inbox-zero/run.py`.
Configuration lives outside the checkout at `~/.config/inbox-zero/config.json`
(mode 0600). Supported keys:

- `INBOX_ACCESS_TOKEN`: random private access key, at least 32 characters.
- `INBOX_ORIGIN`: exact HTTPS browser origin, including port when applicable.
- `INBOX_TAILSCALE_OWNER`: optional exact Tailscale login identity. Only trust this
  behind Tailscale Serve on loopback; Uvicorn proxy-header rewriting is disabled.
- `INBOX_DB`: durable SQLite path; defaults to `~/.local/state/inbox-zero/review.sqlite`.
- `INBOX_GMAIL_CREDENTIALS`: path to an authorized-user Google OAuth JSON file with
  `https://www.googleapis.com/auth/gmail.modify` scope. The ChatGPT connector and
  Google Cloud CLI credentials do not automatically grant the service Gmail access.
- `INBOX_PROTON_CREDENTIALS`: path to a JSON object with `username` and `password`,
  sourced from the existing `proton-bridge-creds` secret.
- `INBOX_PROTON_HOST`, `INBOX_PROTON_PORT`: default to Bridge on `127.0.0.1:1143`.
- `INBOX_PORT`: loopback HTTP port, default 8787.

Install `inbox-zero.service` as a user unit after placing the checkout at
`~/.local/share/inbox-zero`. Serve through a dedicated Tailscale HTTPS port;
never replace an existing handler or enable Funnel for private mail. Persistent
state and credentials remain outside releases. Back up SQLite with its backup API.

For Gmail, enable the Gmail API in your own Google Cloud project and download a
desktop OAuth client JSON. Run `uv run --extra inbox python
deploy/inbox-zero/connect_gmail.py /path/to/client.json` on a machine with a browser.
Authorize the intended mailbox, securely transfer the resulting
`~/.config/inbox-zero/gmail.json` to the service host, set
`INBOX_GMAIL_CREDENTIALS`, and restart the service. Keep both files outside Git.
An imported connector snapshot does not authorize live sync or mailbox writes.

The MCP endpoint is `/mcp`, supporting the same access key as a Bearer token.
`review_inbox` returns the queue and an MCP App resource; `decide_email` applies
explicit owner decisions. The server is private to the tailnet; cloud ChatGPT
cannot connect to this endpoint without a separate supported deployment route.

## Decisions

- Mark read: verify the provider's read flag, then complete review.
- Keep actionable: verify unread, then retain in Actionable.
- Archive: remove from inbox without deleting, then complete review. Bulk actions
  list their exact members and require an explicit click. Proton requires UID MOVE.
- Later: save a future wake time. Expired deferrals reappear in To review.
- Draft reply: persist draft text here; the user sends it through their email app.
- Return to review: reopen the local review decision.

One request ID identifies one exact action. Replays return its saved result;
conflicting reuse and uncertain in-flight operations are rejected. A failed or
unconfirmed mailbox operation never marks the item reviewed. A partial sync never
removes unseen records. Each source retains its last successful batch when refresh
fails. Source counts and freshness distinguish partial imports from complete scans.

Refresh is user initiated. No extra schedules or automatic mailbox mutations are
installed. Gmail and Proton fetch using read-only/PEEK paths. Mail HTML, remote
images, and email instructions are never executed by the UI.

## Verify

`uv run --extra inbox pytest tests/test_inbox_zero.py tests/test_email_triage.py`

Also run Ruff, type checks, deployment validation, a real HTTP/MCP read, and browser
checks at phone and desktop sizes. Provider writes need separately authorized
throwaway messages; the local persistence tests do not establish live Gmail/Proton
mutation success.

Live Proton verification used one temporary message appended through Bridge:
read, unread, and archive were checked against server state, then the test message
was moved to Trash. Authenticated IMAP capabilities must be queried after login;
Bridge's initial capability list omits MOVE. Gmail writes still require a
separate authorization and live test.

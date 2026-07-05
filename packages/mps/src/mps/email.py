"""Proton mail fetch via a local hydroxide IMAP bridge.

Proton doesn't speak IMAP directly — hydroxide (github.com/emersion/hydroxide)
runs on the same box and bridges Proton's API to IMAP on localhost:1143.
Login uses the hydroxide-generated bridge password, not the Proton password.
"""

import datetime
import email
import email.header
import email.utils
import imaplib
import re

from pydantic import BaseModel

SNIPPET_CHARS = 500


class EmailItem(BaseModel):
    """A single inbox message, trimmed to what the hub pipeline needs."""

    message_id: str
    subject: str
    sender_name: str
    sender_address: str
    snippet: str
    received_at: str  # ISO 8601
    unread: bool
    mailbox: str = "INBOX"


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for chunk, charset in email.header.decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def _extract_snippet(msg: email.message.Message) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get(
                "Content-Disposition", ""
            ).startswith("attachment"):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")

    body = re.sub(r"\s+", " ", body).strip()
    return body[:SNIPPET_CHARS]


def fetch_inbox(
    host: str,
    port: int,
    username: str,
    password: str,
    since_days: int = 14,
    limit: int = 200,
    mailbox: str = "INBOX",
) -> list[EmailItem]:
    """Fetch recent messages from the bridge without marking them read."""
    since = (datetime.date.today() - datetime.timedelta(days=since_days)).strftime(
        "%d-%b-%Y"
    )

    conn = imaplib.IMAP4(host, port)
    try:
        conn.login(username, password)
        conn.select(mailbox, readonly=True)

        _, data = conn.search(None, f"(SINCE {since})")
        uids = data[0].split()
        uids = uids[-limit:]

        items: list[EmailItem] = []
        for uid in uids:
            _, msg_data = conn.fetch(uid, "(BODY.PEEK[] FLAGS INTERNALDATE)")
            raw = None
            flags = b""
            for part in msg_data:
                if isinstance(part, tuple):
                    flags += part[0]
                    raw = part[1]
                elif isinstance(part, bytes):
                    flags += part
            if raw is None:
                continue

            msg = email.message_from_bytes(raw)

            message_id = (
                _decode_header(msg.get("Message-ID")) or f"uid-{mailbox}-{uid.decode()}"
            )
            sender_name, sender_address = email.utils.parseaddr(
                _decode_header(msg.get("From"))
            )

            received_at = ""
            date_header = msg.get("Date")
            if date_header:
                parsed = email.utils.parsedate_to_datetime(date_header)
                if parsed:
                    received_at = parsed.astimezone(datetime.UTC).isoformat()

            items.append(
                EmailItem(
                    message_id=message_id,
                    subject=_decode_header(msg.get("Subject")),
                    sender_name=sender_name,
                    sender_address=sender_address,
                    snippet=_extract_snippet(msg),
                    received_at=received_at,
                    unread=b"\\Seen" not in flags,
                    mailbox=mailbox,
                )
            )
        return items
    finally:
        try:
            conn.logout()
        except Exception:
            pass

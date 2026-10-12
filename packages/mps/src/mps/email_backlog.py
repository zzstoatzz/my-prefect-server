"""Read-only, UID-paginated Proton inbox review."""

import contextlib
import datetime
import email
import email.utils
import imaplib
import re
from html.parser import HTMLParser
from typing import Literal

from mps.email import _decode_header


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def message_text(msg: email.message.Message) -> str:
    parts = [p for p in msg.walk() if p.get_content_disposition() != "attachment"]
    for kind in ("text/plain", "text/html"):
        for part in parts:
            if part.get_content_type() != kind:
                continue
            raw = part.get_payload(decode=True)
            if not isinstance(raw, bytes):
                continue
            try:
                text = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            if kind == "text/html":
                parser = _HTMLText()
                parser.feed(text)
                text = " ".join(parser.parts)
            text = re.sub(r"[\u034f\u200b-\u200f\u00ad\ufeff]", "", text)
            if text.strip():
                return re.sub(r"\s+", " ", text).strip()
    return ""


def read_backlog(
    host: str,
    port: int,
    username: str,
    password: str,
    since: str | None,
    batch_size: int = 50,
    before_uid: int | None = None,
    uidvalidity: str | None = None,
    only_uids: list[int] | None = None,
    body_chars: int = 1500,
    mailbox: Literal["INBOX", "Sent"] = "INBOX",
) -> dict:
    if mailbox not in ("INBOX", "Sent"):
        raise ValueError("mailbox must be INBOX or Sent")
    if not 1 <= batch_size <= 100 or not 1 <= body_chars <= 50000:
        raise ValueError("batch_size must be 1..100; body_chars must be 1..50000")
    if before_uid is not None and not uidvalidity:
        raise ValueError("continuation requires uidvalidity from the previous page")
    if only_uids and (len(only_uids) > batch_size or any(uid < 1 for uid in only_uids)):
        raise ValueError("only_uids must contain at most batch_size positive UIDs")
    criterion = "ALL"
    if since:
        day = datetime.date.fromisoformat(since)
        criterion = f"SINCE {day.strftime('%d-%b-%Y')}"
    conn = imaplib.IMAP4(host, port, timeout=60)
    try:
        conn.login(username, password)
        status, _ = conn.select(mailbox, readonly=True)
        if status != "OK":
            raise RuntimeError(f"could not select {mailbox}")
        _, validity = conn.response("UIDVALIDITY")
        current_validity = validity[0].decode() if validity and validity[0] else ""
        if not current_validity or (uidvalidity and uidvalidity != current_validity):
            raise ValueError("mailbox UIDVALIDITY missing or changed; restart review")
        status, data = conn.uid("SEARCH", criterion)
        if status != "OK":
            raise RuntimeError("inbox search failed")
        all_uids = sorted((int(uid) for uid in data[0].split()), reverse=True)
        selected = [uid for uid in all_uids if before_uid is None or uid < before_uid]
        if only_uids is not None:
            wanted = set(only_uids)
            selected = [uid for uid in selected if uid in wanted]
            if set(selected) != wanted:
                raise ValueError("requested UIDs missing from the selected inbox/date range")
        page = selected[:batch_size]
        messages = []
        for uid in page:
            status, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[] FLAGS INTERNALDATE)")
            if status != "OK":
                raise RuntimeError(f"fetch failed for UID {uid}")
            chunks = [part for part in data if isinstance(part, tuple)]
            if len(chunks) != 1 or not isinstance(chunks[0][1], bytes):
                raise RuntimeError(f"missing message body for UID {uid}; retry page")
            metadata, raw = chunks[0]
            metadata += b" ".join(part for part in data if isinstance(part, bytes))
            msg = email.message_from_bytes(raw)
            text = message_text(msg)
            sender_name, sender_address = email.utils.parseaddr(_decode_header(msg.get("From")))
            messages.append(
                {
                    "uid": uid,
                    "message_id": _decode_header(msg.get("Message-ID")),
                    "in_reply_to": _decode_header(msg.get("In-Reply-To")),
                    "sender": sender_name,
                    "address": sender_address,
                    "to": _decode_header(msg.get("To")),
                    "cc": _decode_header(msg.get("Cc")),
                    "subject": _decode_header(msg.get("Subject")),
                    "date": _decode_header(msg.get("Date")),
                    "unread": b"\\Seen" not in metadata,
                    "text": text[:body_chars],
                    "body_chars": len(text),
                    "truncated": len(text) > body_chars,
                    "attachments": [
                        _decode_header(p.get_filename())
                        for p in msg.walk()
                        if p.get_content_disposition() == "attachment"
                    ],
                }
            )
        return {
            "mailbox": mailbox,
            "since": since,
            "uidvalidity": current_validity,
            "total_matching": len(all_uids),
            "remaining": max(0, len(selected) - len(page)),
            "next_before_uid": page[-1] if len(selected) > len(page) else None,
            "messages": messages,
        }
    finally:
        with contextlib.suppress(Exception):
            conn.logout()

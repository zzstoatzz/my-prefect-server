"""Proton mail fetch via a local hydroxide IMAP bridge.

Proton doesn't speak IMAP directly — hydroxide (github.com/emersion/hydroxide)
runs on the same box and bridges Proton's API to IMAP on localhost:1143.
Login uses the hydroxide-generated bridge password, not the Proton password.
"""

import contextlib
import datetime
import email
import email.header
import email.utils
import imaplib
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, get_args

from pydantic import BaseModel
from typesafe_sdk import Choice, ChoiceAnswer

SNIPPET_CHARS = 500

EmailCategory = Literal["personal", "work", "notification", "promotional"]

# one Choice option per category; the descriptions are the rubric Jev reads,
# so they must stand on their own without the question ID.
EMAIL_CATEGORY_CRITERIA: dict[EmailCategory, str] = {
    "personal": "Written by a human directly to the recipient.",
    "work": (
        "Professional correspondence needing attention: invoices, contracts, "
        "recruiter or client mail, account security."
    ),
    "notification": (
        "Automated but informational: mailing lists, forum threads, CI results, "
        "statements ready, receipts."
    ),
    "promotional": "Marketing, sales, product announcements, engagement bait.",
}

CLASSIFY_SNIPPET_CHARS = 200


class EmailClassification(BaseModel):
    """Model-assigned category for one inbox message.

    ``confidence`` is Jev's own certainty in the chosen category (0 to 1, from
    how peaked its probability distribution is). It is recorded for calibration
    and is not yet used to gate anything."""

    message_id: str
    category: EmailCategory
    confidence: float | None = None


EmailRow = tuple[str, str, str, str]
"""(message_id, sender, subject, snippet) as ``mps.db.unclassified_emails`` returns it."""


def _question_id(index: int) -> str:
    return f"category_{index}"


def classification_request(batch: Sequence[EmailRow]) -> tuple[dict[str, Any], dict[str, Choice]]:
    """The Jev state and questions for one batch: the emails as an array, one
    Choice per index pointing at its element by path."""
    state = {
        "emails": [
            {"sender": sender, "subject": subject, "snippet": snippet[:CLASSIFY_SNIPPET_CHARS]}
            for _, sender, subject, snippet in batch
        ]
    }
    questions = {
        _question_id(i): Choice(
            instructions=(
                f"Which category does the inbox email `emails[{i}]` belong to? "
                "Judge who sent it and why from its sender, subject and snippet. "
                "The recipient is a solo software developer."
            ),
            criteria={str(k): v for k, v in EMAIL_CATEGORY_CRITERIA.items()},
        )
        for i in range(len(batch))
    }
    return state, questions


def _as_category(choice: str) -> EmailCategory:
    for category in get_args(EmailCategory):
        if choice == category:
            return category
    raise ValueError(f"category {choice!r} is not one of the offered criteria")


def classifications_from_answers(
    batch: Sequence[EmailRow], answers: Mapping[str, ChoiceAnswer]
) -> list[EmailClassification]:
    """Pair each batch row with its Choice answer. Every row gets exactly one
    classification; a missing answer is a contract violation, not a skip."""
    out: list[EmailClassification] = []
    for i, (message_id, *_rest) in enumerate(batch):
        answer = answers[_question_id(i)]
        out.append(
            EmailClassification(
                message_id=message_id,
                category=_as_category(answer.choice),
                confidence=answer.confidence,
            )
        )
    return out


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


def _extract_text(msg: email.message.Message) -> str:
    """Full normalized text of the first text/plain part."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get(
                "Content-Disposition", ""
            ).startswith("attachment"):
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    break
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")

    return re.sub(r"\s+", " ", body).strip()


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
    since = (datetime.date.today() - datetime.timedelta(days=since_days)).strftime("%d-%b-%Y")

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

            message_id = _decode_header(msg.get("Message-ID")) or f"uid-{mailbox}-{uid.decode()}"
            sender_name, sender_address = email.utils.parseaddr(_decode_header(msg.get("From")))

            received_at = ""
            date_header = msg.get("Date")
            if date_header:
                parsed = email.utils.parsedate_to_datetime(date_header)
                if parsed:
                    received_at = parsed.astimezone(datetime.UTC).isoformat()

            body = _extract_text(msg)
            items.append(
                EmailItem(
                    message_id=message_id,
                    subject=_decode_header(msg.get("Subject")),
                    sender_name=sender_name,
                    sender_address=sender_address,
                    snippet=body[:SNIPPET_CHARS],
                    received_at=received_at,
                    unread=b"\\Seen" not in flags,
                    mailbox=mailbox,
                )
            )
        return items
    finally:
        with contextlib.suppress(Exception):
            conn.logout()

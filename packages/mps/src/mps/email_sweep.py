"""Archive aged bulk and automated mail out of the Proton inbox.

A message leaves INBOX for Archive only when it is older than ``min_age``,
not starred, from an address the owner has never written to, and either
carries list headers, comes from an automated-looking local part, or was
classified notification/promotional with enough confidence. Archive is a
move, never a delete, and unread state is preserved.
"""

import contextlib
import datetime
import email
import email.utils
import imaplib
import re
from collections import Counter
from dataclasses import dataclass

from mps.email import _decode_header

QUIET_CATEGORIES = ("notification", "promotional")
MIN_CONFIDENCE = 0.7
BATCH = 100

_AUTOMATED = re.compile(
    r"(^|[._+-])(no-?reply|do-?not-?reply|notifications?|notify|mailer|alerts?|"
    r"news(letter)?|updates?|marketing|hello|info|team|support|billing|receipts?|"
    r"statements?|events?|digest|invoice)([._+-]|$)",
    re.I,
)


@dataclass(frozen=True)
class Headers:
    uid: int
    message_id: str
    sender: str
    received: datetime.datetime
    flagged: bool
    bulk: bool


def sweep_reason(
    msg: Headers,
    *,
    now: datetime.datetime,
    min_age: datetime.timedelta,
    correspondents: set[str],
    categories: dict[str, tuple[str, float | None]],
) -> str | None:
    if msg.flagged or msg.sender in correspondents or now - msg.received < min_age:
        return None
    if msg.bulk:
        return "bulk"
    category, confidence = categories.get(msg.message_id, (None, None))
    if category in QUIET_CATEGORIES and (confidence or 0) >= MIN_CONFIDENCE:
        return category
    if _AUTOMATED.search(msg.sender.partition("@")[0]):
        return "automated"
    return None


def _check(result):
    status, data = result
    if status != "OK":
        raise RuntimeError("Proton Bridge rejected the mailbox operation")
    return data


def _headers(conn: imaplib.IMAP4, fields: str) -> list[tuple[bytes, email.message.Message]]:
    uids = _check(conn.uid("SEARCH", "ALL"))[0].split()
    out = []
    for i in range(0, len(uids), 200):
        data = _check(
            conn.uid(
                "FETCH",
                b",".join(uids[i : i + 200]).decode(),
                f"(UID FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS ({fields})])",
            )
        )
        out += [(p[0], email.message_from_bytes(p[1])) for p in data if isinstance(p, tuple)]
    return out


def _address(value: str | None) -> str:
    return email.utils.parseaddr(_decode_header(value))[1].lower()


def sweep_inbox(
    host: str,
    port: int,
    username: str,
    password: str,
    categories: dict[str, tuple[str, float | None]],
    min_age_days: int = 7,
    dry_run: bool = False,
    now: datetime.datetime | None = None,
) -> dict:
    if min_age_days < 1:
        raise ValueError("min_age_days must be at least 1")
    now = now or datetime.datetime.now(datetime.UTC)
    conn = imaplib.IMAP4(host, port, timeout=120)
    try:
        _check(conn.login(username, password))
        _check(conn.select('"Sent"', readonly=True))
        correspondents = {
            addr.lower()
            for _, h in _headers(conn, "TO CC")
            for _, addr in email.utils.getaddresses(
                [_decode_header(h.get("To")), _decode_header(h.get("Cc"))]
            )
        }
        _check(conn.select('"INBOX"', readonly=dry_run))
        chosen: dict[int, str] = {}
        reasons: Counter[str] = Counter()
        total = 0
        for meta, h in _headers(conn, "FROM MESSAGE-ID LIST-UNSUBSCRIBE LIST-ID"):
            total += 1
            uid = re.search(rb"UID (\d+)", meta)
            arrived = imaplib.Internaldate2tuple(meta)
            if uid is None or arrived is None:
                raise RuntimeError("Proton Bridge omitted UID or INTERNALDATE")
            msg = Headers(
                uid=int(uid.group(1)),
                message_id=_decode_header(h.get("Message-ID")),
                sender=_address(h.get("From")),
                received=datetime.datetime.fromtimestamp(
                    datetime.datetime(*arrived[:6]).timestamp(), datetime.UTC
                ),
                flagged=b"\\Flagged" in meta,
                bulk=bool(h.get("List-Unsubscribe") or h.get("List-ID")),
            )
            reason = sweep_reason(
                msg,
                now=now,
                min_age=datetime.timedelta(days=min_age_days),
                correspondents=correspondents,
                categories=categories,
            )
            if reason:
                chosen[msg.uid] = msg.message_id
                reasons[reason] += 1
        if chosen and not dry_run:
            capabilities = b" ".join(_check(conn.capability())).upper().split()
            if b"MOVE" not in capabilities:
                raise RuntimeError("Proton Bridge does not support safe archive moves")
            uids = sorted(chosen)
            for i in range(0, len(uids), BATCH):
                uid_set = ",".join(map(str, uids[i : i + BATCH]))
                _check(conn.uid("MOVE", uid_set, '"Archive"'))
                if _check(conn.uid("SEARCH", "UID", uid_set))[0].strip():
                    raise RuntimeError("Proton did not confirm the archive move")
        return {
            "inbox_before": total,
            "archived": 0 if dry_run else len(chosen),
            "would_archive": len(chosen),
            "kept": total - len(chosen),
            "reasons": dict(reasons),
            "message_ids": list(chosen.values()),
            "dry_run": dry_run,
        }
    finally:
        with contextlib.suppress(Exception):
            conn.logout()

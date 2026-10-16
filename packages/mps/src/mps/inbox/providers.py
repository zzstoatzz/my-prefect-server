from __future__ import annotations

import base64
import contextlib
import datetime
import email
import email.policy
import email.utils
import imaplib
import json
import os
import re
from pathlib import Path

import httpx

from mps.email import _decode_header, _extract_text
from mps.inbox.store import Message


class Gmail:
    def __init__(self, credentials_path: str):
        self.credentials_path = credentials_path

    def client(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        credentials = Credentials.from_authorized_user_file(self.credentials_path)
        credentials.refresh(Request())
        return httpx.Client(
            base_url="https://gmail.googleapis.com/gmail/v1/users/me/",
            headers={"Authorization": f"Bearer {credentials.token}"},
            timeout=30,
        )

    def fetch(self) -> tuple[str, list[Message]]:
        with self.client() as client:
            response = client.get("profile")
            response.raise_for_status()
            account = response.json()["emailAddress"]
            ids = []
            page = None
            while True:
                params = {"labelIds": "INBOX", "maxResults": 500}
                if page:
                    params["pageToken"] = page
                response = client.get("messages", params=params)
                response.raise_for_status()
                data = response.json()
                ids.extend(item["id"] for item in data.get("messages", []))
                page = data.get("nextPageToken")
                if not page:
                    break
            messages = []
            for remote_id in ids:
                response = client.get(f"messages/{remote_id}", params={"format": "raw"})
                response.raise_for_status()
                data = response.json()
                raw = data["raw"]
                msg = email.message_from_bytes(
                    base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)),
                    policy=email.policy.default,
                )
                labels = data.get("labelIds", [])
                messages.append(
                    Message(
                        provider="gmail",
                        account=account,
                        remote_id=remote_id,
                        message_id=str(msg.get("Message-ID", "")),
                        sender=_decode_header(msg.get("From")),
                        subject=_decode_header(msg.get("Subject")),
                        body=_extract_text(msg)[:50000],
                        received_at=datetime.datetime.fromtimestamp(
                            int(data["internalDate"]) / 1000, datetime.UTC
                        ).isoformat(),
                        unread="UNREAD" in labels,
                        category="CATEGORY_PROMOTIONS" if "CATEGORY_PROMOTIONS" in labels else "",
                        url=f"https://mail.google.com/mail/u/{account}/#all/{remote_id}",
                    )
                )
            return account, messages

    def apply(self, message: Message, action: str):
        if action not in ("read", "keep", "ignore"):
            return
        add = ["UNREAD"] if action == "keep" else []
        remove = ["UNREAD"] if action == "read" else ["INBOX"] if action == "ignore" else []
        with self.client() as client:
            response = client.get("profile")
            response.raise_for_status()
            if response.json()["emailAddress"].lower() != message.account.lower():
                raise ValueError("Gmail account changed; refresh before acting")
            response = client.post(
                f"messages/{message.remote_id}/modify",
                json={"addLabelIds": add, "removeLabelIds": remove},
            )
            response.raise_for_status()
            response = client.get(f"messages/{message.remote_id}", params={"format": "minimal"})
            response.raise_for_status()
            labels = response.json()["labelIds"]
            if any(label not in labels for label in add) or any(
                label in labels for label in remove
            ):
                raise ValueError("Gmail did not confirm the requested change")


class Proton:
    def __init__(self, credentials_path: str, host: str = "127.0.0.1", port: int = 1143):
        self.credentials_path = credentials_path
        self.host = host
        self.port = port

    @contextlib.contextmanager
    def connection(self, readonly: bool):
        credentials = json.loads(Path(self.credentials_path).read_text())
        conn = imaplib.IMAP4(self.host, self.port, timeout=30)
        try:
            self.check(conn.login(credentials["username"], credentials["password"]))
            self.check(conn.select("INBOX", readonly=readonly))
            validity = conn.response("UIDVALIDITY")[1][0].decode()
            yield conn, credentials["username"], validity
        finally:
            with contextlib.suppress(Exception):
                conn.logout()

    @staticmethod
    def check(result):
        status, data = result
        if status != "OK":
            raise RuntimeError("Proton Bridge rejected the mailbox operation")
        return data

    def fetch(self) -> tuple[str, list[Message]]:
        with self.connection(True) as (conn, account, validity):
            uids = self.check(conn.uid("SEARCH", None, "ALL"))[0].split()
            messages = []
            for uid in uids:
                data = self.check(conn.uid("FETCH", uid, "(BODY.PEEK[] FLAGS INTERNALDATE)"))
                part = next((p for p in data if isinstance(p, tuple)), None)
                if part is None:
                    raise RuntimeError("Proton message disappeared during sync; retry refresh")
                metadata, raw = part
                msg = email.message_from_bytes(raw, policy=email.policy.default)
                received = (
                    email.utils.parsedate_to_datetime(str(msg.get("Date", "")))
                    if msg.get("Date")
                    else None
                )
                if received is None:
                    raise ValueError("Proton message has no parseable date")
                messages.append(
                    Message(
                        provider="proton",
                        account=account,
                        remote_id=f"{validity}:{uid.decode()}",
                        message_id=str(msg.get("Message-ID", "")),
                        sender=_decode_header(msg.get("From")),
                        subject=_decode_header(msg.get("Subject")),
                        body=_extract_text(msg)[:50000],
                        received_at=received.astimezone(datetime.UTC).isoformat(),
                        unread=b"\\Seen" not in metadata,
                        url="https://mail.proton.me/u/0/inbox",
                    )
                )
            return account, messages

    def apply(self, message: Message, action: str):
        if action not in ("read", "keep", "ignore"):
            return
        with self.connection(False) as (conn, account, validity):
            expected, uid = message.remote_id.split(":")
            if expected != validity or account != message.account or not uid.isdigit():
                raise ValueError("Proton mailbox identity changed; refresh before acting")
            if action == "ignore":
                capabilities = {c.upper() for c in conn.capabilities}
                if b"MOVE" not in capabilities:
                    raise ValueError("Proton Bridge does not support safe archive moves")
                self.check(conn.uid("MOVE", uid, "Archive"))
                remaining = self.check(conn.uid("SEARCH", None, "UID", uid))[0]
                if remaining:
                    raise ValueError("Proton did not confirm the archive move")
            else:
                operation = "+FLAGS.SILENT" if action == "read" else "-FLAGS.SILENT"
                self.check(conn.uid("STORE", uid, operation, "(\\Seen)"))
                flags = self.check(conn.uid("FETCH", uid, "(FLAGS)"))
                data = b" ".join(
                    p[0] if isinstance(p, tuple) else p for p in flags if p is not None
                )
                if not re.search(rb"\b" + uid.encode() + rb"\b", data) or (b"\\Seen" in data) != (
                    action == "read"
                ):
                    raise ValueError("Proton did not confirm the read-state change")


def configured_providers():
    providers = {}
    if path := os.getenv("INBOX_GMAIL_CREDENTIALS"):
        providers["gmail"] = Gmail(path)
    if path := os.getenv("INBOX_PROTON_CREDENTIALS"):
        providers["proton"] = Proton(
            path,
            os.getenv("INBOX_PROTON_HOST", "127.0.0.1"),
            int(os.getenv("INBOX_PROTON_PORT", "1143")),
        )
    return providers

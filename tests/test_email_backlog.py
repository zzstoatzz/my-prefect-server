import socketserver
import threading
from email.message import EmailMessage

import pytest
from mps.email_backlog import message_text, read_backlog


@pytest.fixture
def inbox_server():
    commands = []
    message = EmailMessage()
    message["Subject"] = "Invoice due tomorrow"
    message.set_content("Please review the invoice.")
    raw = message.as_bytes()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b"* OK test inbox\r\n")
            while line := self.rfile.readline():
                text = line.decode().strip()
                commands.append(text)
                tag, command, *args = text.split()
                if command == "CAPABILITY":
                    self.wfile.write(b"* CAPABILITY IMAP4rev1\r\n")
                elif command == "EXAMINE":
                    self.wfile.write(b"* 3 EXISTS\r\n* OK [UIDVALIDITY 42] stable\r\n")
                elif command == "UID" and args[0] == "SEARCH":
                    self.wfile.write(b"* SEARCH 11 20 35\r\n")
                elif command == "UID" and args[0] == "FETCH":
                    self.wfile.write(
                        f"* 1 FETCH (UID {args[1]} BODY[] {{{len(raw)}}}\r\n".encode()
                        + raw
                        + b" FLAGS (\\Seen))\r\n"
                    )
                elif command == "LOGOUT":
                    self.wfile.write(b"* BYE done\r\n")
                    self.wfile.write(f"{tag} OK logout\r\n".encode())
                    break
                elif command != "LOGIN":
                    self.wfile.write(f"{tag} BAD unexpected command\r\n".encode())
                    continue
                self.wfile.write(f"{tag} OK done\r\n".encode())

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield server.server_address, commands
        server.shutdown()
        thread.join()


def test_real_imap_pagination_preserves_seen_and_requires_stable_mailbox(inbox_server):
    (host, port), commands = inbox_server
    first = read_backlog(host, port, "test", "test", "2026-07-14", batch_size=2, body_chars=10)
    assert [m["uid"] for m in first["messages"]] == [35, 20]
    assert first["total_matching"] == 3
    assert first["remaining"] == 1
    assert first["messages"][0]["truncated"] is True
    assert first["messages"][0]["unread"] is False
    second = read_backlog(
        host,
        port,
        "test",
        "test",
        "2026-07-14",
        batch_size=2,
        before_uid=first["next_before_uid"],
        uidvalidity=first["uidvalidity"],
    )
    assert [m["uid"] for m in second["messages"]] == [11]
    assert second["next_before_uid"] is None
    assert second["remaining"] == 0
    assert all("STORE" not in c and " SELECT " not in c for c in commands)
    assert all("BODY.PEEK[]" in c for c in commands if " FETCH " in c)
    with pytest.raises(ValueError, match="changed"):
        read_backlog(host, port, "test", "test", None, before_uid=20, uidvalidity="wrong")
    with pytest.raises(ValueError, match="missing"):
        read_backlog(host, port, "test", "test", None, only_uids=[99])


def test_html_only_mail_and_attachment_exclusion():
    msg = EmailMessage()
    msg.set_content("<style>hidden</style><p>Pay by Friday &amp; confirm.</p>", subtype="html")
    msg.add_attachment("not the message", filename="invoice.txt")
    assert message_text(msg) == "Pay by Friday & confirm."

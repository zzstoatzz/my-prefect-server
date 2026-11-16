import datetime
import socketserver
import threading

import pytest
from mps.email_sweep import Headers, sweep_inbox, sweep_reason

NOW = datetime.datetime(2026, 11, 16, tzinfo=datetime.UTC)
OLD = NOW - datetime.timedelta(days=30)


def headers(**kwargs):
    base = {
        "uid": 1,
        "message_id": "<m@x>",
        "sender": "noreply@shop.example",
        "received": OLD,
        "flagged": False,
        "bulk": False,
    }
    return Headers(**{**base, **kwargs})


def reason(msg, correspondents=(), categories=None):
    return sweep_reason(
        msg,
        now=NOW,
        min_age=datetime.timedelta(days=7),
        correspondents=set(correspondents),
        categories=categories or {},
    )


@pytest.mark.parametrize(
    ("msg", "expected"),
    [
        (headers(bulk=True, sender="friend@gmail.com"), "bulk"),
        (headers(), "automated"),
        (headers(sender="dispatch.notifications@x.io"), "automated"),
        (headers(sender="alice@example.com"), None),
        (headers(sender="infodesk@example.com"), None),
        (headers(received=NOW - datetime.timedelta(days=2), bulk=True), None),
        (headers(flagged=True, bulk=True), None),
    ],
)
def test_sweep_reason(msg, expected):
    assert reason(msg) == expected


def test_correspondents_are_never_swept():
    msg = headers(bulk=True, sender="bryan@example.com")
    assert reason(msg, correspondents=["bryan@example.com"]) is None


def test_classifier_needs_confidence():
    msg = headers(sender="alice@example.com")
    assert reason(msg, categories={"<m@x>": ("promotional", 0.9)}) == "promotional"
    assert reason(msg, categories={"<m@x>": ("promotional", 0.5)}) is None
    assert reason(msg, categories={"<m@x>": ("promotional", None)}) is None
    assert reason(msg, categories={"<m@x>": ("personal", 0.99)}) is None


INBOX = {
    11: b"From: Shop <noreply@shop.example>\r\nMessage-ID: <a@x>\r\n\r\n",
    12: b"From: Alice <alice@example.com>\r\nMessage-ID: <b@x>\r\n\r\n",
    13: b"From: News <hi@list.example>\r\nList-ID: <l.example>\r\nMessage-ID: <c@x>\r\n\r\n",
    14: b"From: Bob <bob@example.com>\r\nList-ID: <l.example>\r\nMessage-ID: <d@x>\r\n\r\n",
}
SENT = b"To: Bob <bob@example.com>\r\n\r\n"


@pytest.fixture
def bridge():
    commands, state = [], {"box": None, "inbox": dict(INBOX)}

    class Handler(socketserver.StreamRequestHandler):
        def send(self, data: bytes):
            self.wfile.write(data)

        def handle(self):
            self.send(b"* OK test bridge\r\n")
            while line := self.rfile.readline():
                text = line.decode().strip()
                commands.append(text)
                tag, command, *args = text.split()
                if command == "CAPABILITY":
                    self.send(b"* CAPABILITY IMAP4rev1 MOVE\r\n")
                elif command in ("EXAMINE", "SELECT"):
                    state["box"] = args[0].strip('"')
                    self.send(b"* 4 EXISTS\r\n* OK [UIDVALIDITY 42] stable\r\n")
                elif command == "UID" and args[0] == "SEARCH":
                    box = state["inbox"] if state["box"] == "INBOX" else {1: SENT}
                    if args[1] == "UID":
                        wanted = {int(u) for u in args[2].split(",")}
                        box = {u: v for u, v in box.items() if u in wanted}
                    self.send(b"* SEARCH " + " ".join(map(str, box)).encode() + b"\r\n")
                elif command == "UID" and args[0] == "FETCH":
                    box = state["inbox"] if state["box"] == "INBOX" else {1: SENT}
                    for uid in map(int, args[1].split(",")):
                        raw = box[uid]
                        self.send(
                            f'* {uid} FETCH (UID {uid} FLAGS () INTERNALDATE "01-Oct-2026 10:00:00 +0000" '
                            f"BODY[HEADER] {{{len(raw)}}}\r\n".encode()
                            + raw
                            + b")\r\n"
                        )
                elif command == "UID" and args[0] == "MOVE":
                    for uid in map(int, args[1].split(",")):
                        state["inbox"].pop(uid)
                elif command == "LOGOUT":
                    self.send(b"* BYE done\r\n" + f"{tag} OK logout\r\n".encode())
                    break
                elif command != "LOGIN":
                    self.send(f"{tag} BAD unexpected\r\n".encode())
                    continue
                self.send(f"{tag} OK done\r\n".encode())

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield server.server_address, commands, state
        server.shutdown()
        thread.join()


def test_dry_run_reads_only(bridge):
    (host, port), commands, state = bridge
    result = sweep_inbox(host, port, "u", "p", {}, dry_run=True, now=NOW)
    assert result["would_archive"] == 2 and result["archived"] == 0
    assert sorted(result["message_ids"]) == ["<a@x>", "<c@x>"]
    assert not any(" MOVE " in c or " SELECT " in c for c in commands)
    assert len(state["inbox"]) == 4


def test_sweep_moves_to_archive_and_keeps_people(bridge):
    (host, port), commands, state = bridge
    result = sweep_inbox(host, port, "u", "p", {}, now=NOW)
    assert result["archived"] == 2
    assert result["reasons"] == {"automated": 1, "bulk": 1}
    assert sorted(state["inbox"]) == [12, 14]
    assert any(c.endswith('UID MOVE 11,13 "Archive"') for c in commands)

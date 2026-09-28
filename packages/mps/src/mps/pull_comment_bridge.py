"""hold stream.waow.tech open and turn revision requests into Prefect events.

runs under systemd on heavypad (deploy/pull-comment-bridge/). every comment
`mps.pull_comments.relevant_comment` accepts becomes one
`autofix.revise-requested` event; an automation starts autofix-revise from it.

the cursor file advances only after a comment is handled, so a failed emit
replays from that event on reconnect. anything still missed is picked up by
the hourly `watch-tangled-pulls` reconcile.

a localhost /health endpoint answers 503 once the subscription has been down
longer than STALE_AFTER_S. fleet-health (also on heavypad) checks it, so a
dead bridge pages through `fleet unhealthy -> discord` rather than dying
quietly.

    python -m mps.pull_comment_bridge --state ~/.local/state/pull-comment-bridge/cursor
"""

import argparse
import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import websockets
from prefect.events import Event
from prefect.events.clients import get_events_client

from mps.pull_comments import (
    load_handled,
    mark_handled,
    relevant_comment,
    revise_event,
    subscribe_url,
)

log = logging.getLogger("pull-comment-bridge")

HEALTH_PORT = 8791
STALE_AFTER_S = 180
BACKOFF_MAX_S = 60


@dataclass
class Status:
    cursor: int | None = None
    connected_since: float | None = None
    disconnected_since: float | None = field(default_factory=time.time)
    last_event_at: float | None = None
    emitted: int = 0
    last_error: str | None = None

    def healthy(self, now: float) -> bool:
        if self.connected_since is not None:
            return True
        return self.disconnected_since is not None and now - self.disconnected_since < STALE_AFTER_S

    def connected(self) -> None:
        self.connected_since, self.disconnected_since, self.last_error = time.time(), None, None

    def disconnected(self, error: str | None) -> None:
        if self.connected_since is not None:
            self.connected_since, self.disconnected_since = None, time.time()
        self.last_error = error


def read_cursor(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def write_cursor(path: Path, cursor: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(str(cursor))
    tmp.replace(path)


async def deliver(comment: dict[str, str], status: Status) -> None:
    if comment["uri"] in await load_handled():
        log.info("already handled %s", comment["uri"])
        return
    async with get_events_client() as client:
        await client.emit(Event(**revise_event(comment)))
    await mark_handled([comment["uri"]])
    status.emitted += 1
    log.info("revise requested for %s by %s", comment["pull"], comment["uri"])


async def handle_message(raw: str | bytes, status: Status, state: Path) -> None:
    event = json.loads(raw)
    status.last_event_at = time.time()
    if comment := relevant_comment(event):
        await deliver(comment, status)
    if time_us := event.get("time_us"):
        status.cursor = time_us
        write_cursor(state, time_us)


async def consume(status: Status, state: Path) -> None:
    async with websockets.connect(subscribe_url(status.cursor), open_timeout=15) as ws:
        status.connected()
        log.info("subscribed from cursor %s", status.cursor)
        async for raw in ws:
            await handle_message(raw, status, state)


async def serve_health(status: Status, port: int) -> asyncio.Server:
    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()
        ok = status.healthy(time.time())
        body = json.dumps(asdict(status)).encode()
        head = "200 OK" if ok else "503 Service Unavailable"
        writer.write(
            f"HTTP/1.1 {head}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()

    return await asyncio.start_server(respond, "127.0.0.1", port)


async def run(state: Path, port: int) -> None:
    status = Status(cursor=read_cursor(state))
    server = await serve_health(status, port)
    backoff = 1
    async with server:
        while True:
            started = time.time()
            try:
                await consume(status, state)
                status.disconnected("stream closed the connection")
            except Exception as exc:
                status.disconnected(repr(exc))
                log.warning("subscription dropped: %r", exc)
            if time.time() - started > BACKOFF_MAX_S:
                backoff = 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--state", type=Path, required=True, help="cursor file")
    parser.add_argument("--port", type=int, default=HEALTH_PORT)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(run(args.state.expanduser(), args.port))


if __name__ == "__main__":
    main()

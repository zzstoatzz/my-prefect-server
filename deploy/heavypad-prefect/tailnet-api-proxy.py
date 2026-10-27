"""Forward only HeavyPad's tailnet listener to its loopback Prefect API."""

import asyncio


async def forward(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, OSError):
        pass


async def handle(reader, writer):
    upstream = None
    try:
        upstream_reader, upstream = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", 14200), timeout=5
        )
        await asyncio.gather(forward(reader, upstream), forward(upstream_reader, writer))
    except (ConnectionError, OSError, TimeoutError):
        pass
    finally:
        writer.close()
        if upstream:
            upstream.close()


async def main():
    server = await asyncio.start_server(handle, "100.96.216.23", 14200)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())

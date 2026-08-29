import struct

import pytest
import zstandard

from flows.strata import HEADER_LEN, parse_collection_index, parse_header


def build_header(*, checksum: int = 0x20F0665E7822A74C, event_count: int = 3503420, collection_index_offset: int = 269970000) -> bytes:
    raw = bytearray(HEADER_LEN)
    raw[0:4] = b"jss0"
    struct.pack_into("<Q", raw, 4, checksum)
    struct.pack_into("<HII", raw, 12, 1, 858, event_count)
    struct.pack_into("<QQ", raw, 26, 1, 3506746)
    struct.pack_into("<Q", raw, 82, collection_index_offset)
    return bytes(raw)


def build_collection_index(collections: list[tuple[str, int]], block_count: int = 3) -> bytes:
    table = b"".join(struct.pack("<BI", len(n), c) + n.encode() for n, c in collections)
    bitmask_len = (len(collections) + 7) // 8
    body = table + bytes(block_count * bitmask_len)
    return struct.pack("<IIII", len(collections), block_count, bitmask_len, len(body)) + zstandard.ZstdCompressor().compress(body)


def test_parse_header_reads_offsets_and_counts():
    h = parse_header(build_header())
    assert h.checksum == 0x20F0665E7822A74C
    assert h.block_count == 858
    assert h.event_count == 3503420
    assert (h.min_seq, h.max_seq) == (1, 3506746)
    assert h.collection_index_offset == 269970000


def test_parse_header_rejects_wrong_magic():
    with pytest.raises(ValueError):
        parse_header(b"nope" + bytes(HEADER_LEN - 4))


def test_parse_collection_index_round_trips_counts():
    cols = [("app.bsky.feed.like", 1885670), ("app.bsky.feed.post", 620964), ("$account", 0)]
    assert parse_collection_index(build_collection_index(cols)) == cols


def test_parse_collection_index_rejects_size_mismatch():
    raw = bytearray(build_collection_index([("app.bsky.feed.post", 1)]))
    struct.pack_into("<I", raw, 12, 999)
    with pytest.raises(ValueError):
        parse_collection_index(bytes(raw))


def test_every_request_carries_the_flow_user_agent():
    from flows.strata import USER_AGENT, request

    plain = request("https://strata.zat.dev/api/progress")
    posted = request("https://strata.zat.dev/ingest", headers={"Authorization": "Bearer x"}, data=b"{}", method="POST")
    assert plain.get_header("User-agent") == USER_AGENT
    assert posted.get_header("User-agent") == USER_AGENT
    assert posted.get_header("Authorization") == "Bearer x"
    assert posted.get_method() == "POST"

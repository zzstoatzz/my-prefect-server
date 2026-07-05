"""Tests for mps.email header/body parsing helpers."""

from email.message import EmailMessage

from mps.email import SNIPPET_CHARS, _decode_header, _extract_snippet


def test_decode_header_plain():
    assert _decode_header("hello world") == "hello world"


def test_decode_header_none():
    assert _decode_header(None) == ""


def test_decode_header_encoded():
    # RFC 2047 encoded-word (utf-8 base64 "héllo")
    assert _decode_header("=?utf-8?b?aMOpbGxv?=") == "héllo"


def test_extract_snippet_plain():
    msg = EmailMessage()
    msg.set_content("line one\nline two\n\n   spaced   ")
    assert _extract_snippet(msg) == "line one line two spaced"


def test_extract_snippet_multipart_prefers_text_plain():
    msg = EmailMessage()
    msg.set_content("plain body")
    msg.add_alternative("<p>html body</p>", subtype="html")
    assert _extract_snippet(msg) == "plain body"


def test_extract_snippet_truncates():
    msg = EmailMessage()
    msg.set_content("x" * (SNIPPET_CHARS * 2))
    assert len(_extract_snippet(msg)) == SNIPPET_CHARS

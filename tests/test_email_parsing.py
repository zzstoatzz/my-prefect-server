"""Tests for mps.email header/body parsing helpers."""

from email.message import EmailMessage

from mps.email import SNIPPET_CHARS, _decode_header, _extract_text, _is_bulk


def test_decode_header_plain():
    assert _decode_header("hello world") == "hello world"


def test_decode_header_none():
    assert _decode_header(None) == ""


def test_decode_header_encoded():
    # RFC 2047 encoded-word (utf-8 base64 "héllo")
    assert _decode_header("=?utf-8?b?aMOpbGxv?=") == "héllo"


def test_extract_text_plain():
    msg = EmailMessage()
    msg.set_content("line one\nline two\n\n   spaced   ")
    assert _extract_text(msg) == "line one line two spaced"


def test_extract_text_multipart_prefers_text_plain():
    msg = EmailMessage()
    msg.set_content("plain body")
    msg.add_alternative("<p>html body</p>", subtype="html")
    assert _extract_text(msg) == "plain body"


def test_snippet_truncation():
    msg = EmailMessage()
    msg.set_content("x" * (SNIPPET_CHARS * 2))
    assert len(_extract_text(msg)[:SNIPPET_CHARS]) == SNIPPET_CHARS


def test_is_bulk_list_unsubscribe_header():
    msg = EmailMessage()
    msg["List-Unsubscribe"] = "<https://example.com/unsub>"
    assert _is_bulk(msg, "", "someone@example.com")


def test_is_bulk_precedence_header():
    msg = EmailMessage()
    msg["Precedence"] = "Bulk"
    assert _is_bulk(msg, "", "someone@example.com")


def test_is_bulk_unsubscribe_in_body():
    # hydroxide drops list headers, so body boilerplate is the main signal
    body = "Big savings await! To unsubscribe, click here."
    assert _is_bulk(EmailMessage(), body, "deals@shop.example.com")


def test_is_bulk_noreply_sender():
    assert _is_bulk(EmailMessage(), "meeting notes", "noreply@service.example.com")
    assert _is_bulk(EmailMessage(), "", "customerservice@e.progressive.com")


def test_is_bulk_personal_mail_is_not():
    msg = EmailMessage()
    msg["From"] = "a friend <friend@example.com>"
    assert not _is_bulk(msg, "hey, want to grab lunch tomorrow?", "friend@example.com")

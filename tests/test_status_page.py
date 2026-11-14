from datetime import UTC, datetime

import pytest

from flows.status_page import BODY_LIMIT, incidents, transitions

NOW = datetime(2026, 11, 14, 1, 0, tzinfo=UTC)
OPENED = "2026-11-14T00:45:38.866+00:00"
INVESTIGATING = (
    "<p><small>Nov <var data-var='date'> 14</var>, <var data-var='time'>00:45:38</var> GMT+0"
    "</small><br /><strong>Investigating</strong> -\n  Some users may experience down time"
    " or instability.</p>"
)
RESOLVED = (
    "<p><small>Nov <var data-var='date'> 14</var>, <var data-var='time'>01:30:00</var> GMT+0"
    "</small><br /><strong>Resolved</strong> -\n  Service is back &amp; stable.</p>"
)


def entry(kind, ident, updated, title, content):
    return f"""
<entry>
  <id>tag:status.bsky.app,2005:{kind}/{ident}</id>
  <published>{OPENED}</published>
  <updated>{updated}</updated>
  <link rel="alternate" type="text/html" href="https://status.bsky.app/{kind.lower()}/{ident}"/>
  <title>{title}</title>
  <content type="html"><![CDATA[
    <p><strong>Type:</strong> {kind}</p>
    <p><strong>Affected Components:</strong> Website, App, and API</p>
    {content}
  ]]></content>
</entry>"""


def feed(*entries):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xml:lang="en-US" xmlns="http://www.w3.org/2005/Atom">'
        "<title>Bluesky Status - Incident history</title>" + "".join(entries) + "</feed>"
    )


def step(state, *entries):
    return transitions("bluesky", state, incidents(feed(*entries)), NOW)


def test_newest_update_is_read_and_maintenance_is_left_out():
    found = incidents(
        feed(
            entry(
                "Incident",
                "abc123",
                "2026-11-14T01:30:00+00:00",
                "Site Instability",
                RESOLVED + INVESTIGATING,
            ),
            entry("Maintenance", "mnt456", OPENED, "PDS Maintenance", INVESTIGATING),
        )
    )
    assert found == [
        {
            "id": "abc123",
            "title": "Site Instability",
            "url": "https://status.bsky.app/incident/abc123",
            "updated": "2026-11-14T01:30:00+00:00",
            "status": "Resolved",
            "body": "Service is back & stable.",
        }
    ]


def test_open_incident_alerts_on_first_sight_then_only_on_new_updates():
    opened = entry("Incident", "abc123", OPENED, "Site Instability", INVESTIGATING)
    state, events = step({}, opened)
    assert [e["event"] for e in events] == ["status-page.incident"]
    assert events[0]["resource"]["prefect.resource.id"] == "status-page.bluesky.abc123"
    assert events[0]["payload"]["summary"] == (
        "bluesky · Site Instability · Investigating: Some users may experience down time"
        " or instability. https://status.bsky.app/incident/abc123"
    )
    state, events = step(state, opened)
    assert events == []

    identified = INVESTIGATING.replace("Investigating", "Identified")
    state, events = step(
        state,
        entry("Incident", "abc123", "2026-11-14T01:10:00+00:00", "Site Instability", identified),
    )
    assert [e["payload"]["status"] for e in events] == ["Identified"]


def test_resolution_recovers_once_and_later_edits_stay_quiet():
    state, first = step({}, entry("Incident", "abc123", OPENED, "Site Instability", INVESTIGATING))
    state, events = step(
        state,
        entry(
            "Incident",
            "abc123",
            "2026-11-14T01:30:00+00:00",
            "Site Instability",
            RESOLVED + INVESTIGATING,
        ),
    )
    assert [e["event"] for e in events] == ["status-page.recovered"]
    assert events[0]["resource"] == first[0]["resource"]
    assert events[0]["id"] != first[0]["id"]
    state, events = step(
        state,
        entry(
            "Incident",
            "abc123",
            "2026-11-14T03:00:00+00:00",
            "Site Instability",
            RESOLVED + INVESTIGATING,
        ),
    )
    assert events == []
    assert state == {"abc123": {"updated": "2026-11-14T03:00:00+00:00", "closed": True}}


def test_history_seeds_silently_and_state_drops_incidents_that_left_the_feed():
    state, events = step(
        {"gone": {"updated": OPENED, "closed": True}},
        entry("Incident", "old", "2026-11-01T00:00:00+00:00", "Old", RESOLVED),
    )
    assert events == []
    assert state == {"old": {"updated": "2026-11-01T00:00:00+00:00", "closed": True}}


def test_resending_the_same_update_keeps_its_event_id():
    opened = entry("Incident", "abc123", OPENED, "Site Instability", INVESTIGATING)
    _, first = step({}, opened)
    _, again = step({}, opened)
    assert first[0]["id"] == again[0]["id"]


def test_long_updates_are_cut_and_markup_is_dropped():
    long = INVESTIGATING.replace("Some users", "<a href='https://x.test'>" + "word " * 300 + "</a>")
    found = incidents(feed(entry("Incident", "abc123", OPENED, "Site Instability", long)))
    assert len(found[0]["body"]) <= BODY_LIMIT
    assert "<" not in found[0]["body"]


def test_an_incident_without_a_readable_update_fails_the_run():
    with pytest.raises(ValueError, match="abc123"):
        incidents(feed(entry("Incident", "abc123", OPENED, "Site Instability", "<p>nothing</p>")))

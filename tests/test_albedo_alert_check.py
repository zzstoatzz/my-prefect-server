import pytest

from flows import albedo_alert_check as check

INCIDENT = {
    "last_fired": "2026-11-11T21:03:21.200833Z",
    "updates": [
        {"at": "2026-11-11T20:00:00Z", "agent": "albedo", "action": "alert.noted", "note": "x"},
        {
            "at": "2026-11-12T08:00:00Z",
            "agent": "claude-code",
            "action": "alert.noted",
            "note": "y",
        },
    ],
}


def test_a_note_before_the_last_firing_needs_a_fresh_check():
    assert not check.checked_since_last_firing(INCIDENT)


def test_a_note_after_the_last_firing_is_enough():
    later = {"at": "2026-11-12T09:00:00+00:00", "agent": "albedo", "action": "alert.noted"}
    assert check.checked_since_last_firing({**INCIDENT, "updates": [*INCIDENT["updates"], later]})


def test_finding_is_the_last_json_object_in_the_output():
    output = (
        'I fetched {"status": 503} from the endpoint.\n'
        '{"matters": true, "message": "typeahead freshness has returned 503 since 21:03Z."}'
    )
    finding = check.parse_finding(output)
    assert finding.matters is True
    assert finding.message.startswith("typeahead freshness")


def test_output_without_a_finding_is_an_error():
    with pytest.raises(ValueError):
        check.parse_finding('curl said {"status": 503}')

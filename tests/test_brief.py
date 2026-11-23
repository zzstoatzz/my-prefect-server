from mps.briefing import SectionAccent, SectionIcon

from flows.brief import SYSTEM_PROMPT


def test_prompt_names_every_allowed_icon_and_accent():
    for value in [*SectionIcon, *SectionAccent]:
        assert value.value in SYSTEM_PROMPT


def test_prompt_steers_bugs_away_from_a_nonexistent_icon():
    assert "bug" not in {icon.value for icon in SectionIcon}
    assert "use alert for bugs" in SYSTEM_PROMPT

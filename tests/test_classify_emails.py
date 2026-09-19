"""classify-emails on Jev: request shape, answer mapping, persistence, spend.

Regression cover for the 2026-09-18 move from Claude Haiku to TypeSafe's Jev.
Jev takes the batch once as state and one Choice per email; the answers must
map back to message_ids by index, carry Jev's confidence into DuckDB, and be
priced per input token.
"""

import json
from pathlib import Path
from unittest import mock

import duckdb
import pytest
from genai_prices.types import Usage
from mps.db import unclassified_emails, write_email_classifications
from mps.email import (
    CLASSIFY_SNIPPET_CHARS,
    EMAIL_CATEGORY_CRITERIA,
    EmailClassification,
    classification_request,
    classifications_from_answers,
)
from mps.spend import record_usage
from typesafe_sdk import ChoiceAnswer

BATCH = [
    ("m1", "alice@example.com", "lunch?", "are you around thursday"),
    ("m2", "billing@stripe.com", "Your invoice is ready", "Invoice INV-42 is now available " * 20),
    ("m3", "deals@shop.example", "50% off everything", "last chance to save"),
]


def _answer(choice: str, confidence: float) -> ChoiceAnswer:
    rest = [k for k in EMAIL_CATEGORY_CRITERIA if k != choice]
    probabilities = {choice: confidence, **{k: (1 - confidence) / len(rest) for k in rest}}
    return ChoiceAnswer(choice=choice, probabilities=probabilities, confidence=confidence)


def test_request_puts_emails_in_state_and_one_choice_per_index():
    state, questions = classification_request(BATCH)

    assert [e["sender"] for e in state["emails"]] == [row[1] for row in BATCH]
    assert len(state["emails"][1]["snippet"]) == CLASSIFY_SNIPPET_CHARS
    assert set(questions) == {"category_0", "category_1", "category_2"}
    for i, question in enumerate(questions.values()):
        # the ID is not sent to the model: the instruction itself must point at
        # the email by path, and the criteria must be the full category rubric
        assert f"`emails[{i}]`" in str(question.instructions)
        assert question.criteria == EMAIL_CATEGORY_CRITERIA


def test_answers_map_back_to_message_ids_with_confidence():
    answers = {
        "category_0": _answer("personal", 0.97),
        "category_1": _answer("work", 0.81),
        "category_2": _answer("promotional", 0.55),
    }
    out = classifications_from_answers(BATCH, answers)
    assert out == [
        EmailClassification(message_id="m1", category="personal", confidence=0.97),
        EmailClassification(message_id="m2", category="work", confidence=0.81),
        EmailClassification(message_id="m3", category="promotional", confidence=0.55),
    ]


def test_missing_answer_is_an_error_not_a_skip():
    with pytest.raises(KeyError):
        classifications_from_answers(BATCH, {"category_0": _answer("personal", 0.9)})


def test_persist_adds_confidence_to_an_existing_table(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("PREFECT_API_URL", raising=False)
    db_path = str(tmp_path / "analytics.duckdb")
    con = duckdb.connect(db_path)
    # the table as dbt's pre_hook and the Haiku-era writer created it: no confidence column
    con.execute(
        "CREATE TABLE raw_email_classifications "
        "(message_id VARCHAR PRIMARY KEY, category VARCHAR, classified_at TIMESTAMP DEFAULT now())"
    )
    con.execute("INSERT INTO raw_email_classifications VALUES ('old', 'work', now())")
    con.execute(
        "CREATE TABLE raw_emails (message_id VARCHAR, sender_address VARCHAR, subject VARCHAR, snippet VARCHAR)"
    )
    con.execute("INSERT INTO raw_emails VALUES ('old', 'a', 's', 'x'), ('new', 'b', 't', 'y')")
    con.close()

    with mock.patch("prefect.settings.PREFECT_API_URL") as setting:
        setting.value.return_value = None
        total = write_email_classifications(
            [EmailClassification(message_id="new", category="personal", confidence=0.9)],
            db_path,
        )
    assert total == 2

    con = duckdb.connect(db_path, read_only=True)
    rows = con.execute(
        "SELECT message_id, category, confidence FROM raw_email_classifications ORDER BY message_id"
    ).fetchall()
    con.close()
    assert rows == [("new", "personal", 0.9), ("old", "work", None)]
    assert unclassified_emails(db_path) == []


def test_typesafe_usage_is_priced_per_input_token(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    record_usage(
        log_path=str(log_path),
        task_name="classify_email_batch",
        provider="typesafe",
        model="jev-1.13.0",
        usage=Usage(input_tokens=1_000_000, output_tokens=500),
    )
    [line] = log_path.read_text(encoding="utf-8").splitlines()
    event = json.loads(line)
    assert event["input_cost_usd"] == pytest.approx(0.042)
    assert event["output_cost_usd"] == 0
    assert event["total_cost_usd"] == pytest.approx(0.042)

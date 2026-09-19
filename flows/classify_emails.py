"""
Classify unclassified inbox emails (personal / work / notification /
promotional) so scoring can down-weight promotional noise.

Chained: ingest fetches + persists raw emails, this flow enriches them, then
transform's dbt build joins the categories in. Split out of ingest so a
classifier outage can't fail ingestion of the other sources, and so ingest
stays pure fetch+persist.

The classifier is TypeSafe's Jev (a System One model): one request per batch
carries the emails as state and one Choice question per email, evaluated in
parallel. Jev returns a category plus its own confidence, which is persisted
for calibration. It replaced Claude Haiku on 2026-09-18.

Cache policy: each batch is keyed by its message_ids — a batch never hits the
model twice within 24h, so the common re-run is seconds.
"""

import datetime
import hashlib
import os
from dataclasses import dataclass
from typing import Any

from genai_prices.types import Usage
from mps.blocks import secret_sync
from mps.db import unclassified_emails, write_email_classifications
from mps.email import (
    EmailClassification,
    EmailRow,
    classification_request,
    classifications_from_answers,
)
from mps.spend import record_usage
from prefect import flow, get_run_logger, task, unmapped
from prefect.cache_policies import CachePolicy
from prefect.context import TaskRunContext

# bump to invalidate cached classifications. v4: Jev replaced Haiku, so a
# batch cached under v3 in the last 24h is re-judged rather than replayed.
_CACHE_VERSION = "v4"

TYPESAFE_MODEL = "jev-latest"


def _db_path() -> str:
    return os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )


CLASSIFY_BATCH_SIZE = 25


@dataclass
class ByEmailBatch(CachePolicy):
    """Cache key is the batch's message_ids — same batch never hits the model twice."""

    def compute_key(
        self,
        task_ctx: TaskRunContext,
        inputs: dict[str, Any],
        flow_parameters: dict[str, Any],
        **kwargs: Any,
    ) -> str | None:
        batch = inputs.get("batch")
        if not batch:
            return None
        h = hashlib.md5("|".join(m[0] for m in batch).encode()).hexdigest()[:12]
        return f"email-classify/{_CACHE_VERSION}/{h}"


@task
def load_unclassified_emails() -> list[EmailRow]:
    return unclassified_emails(_db_path())


@task(
    cache_policy=ByEmailBatch(),
    cache_expiration=datetime.timedelta(hours=24),
    persist_result=True,
    result_serializer="json",
    retries=3,
    retry_delay_seconds=[2, 5, 10],
    retry_jitter_factor=1,
)
def classify_email_batch(batch: list[EmailRow], api_key: str) -> list[EmailClassification]:
    """Jev-classify one batch of emails in a single request. Cached by the batch's message_ids."""
    from typesafe_sdk import TypeSafeClient

    state, questions = classification_request(batch)
    with TypeSafeClient(api_key=api_key, model=TYPESAFE_MODEL) as client:
        response = client.system_one(state=state, questions=questions)
    record_usage(
        task_name="classify_email_batch",
        provider="typesafe",
        model=response.model,
        usage=Usage(
            input_tokens=response.usage.input_tokens or 0,
            output_tokens=response.usage.output_tokens or 0,
        ),
        metadata={"email_count": len(batch)},
    )
    return classifications_from_answers(batch, response.choices)


@task
def persist_email_classifications(items: list[EmailClassification]) -> int:
    return write_email_classifications(items, _db_path())


@flow(name="classify-emails", log_prints=True, timeout_seconds=900)
def classify_emails():
    logger = get_run_logger()

    pending = load_unclassified_emails()
    if not pending:
        logger.info("no unclassified emails")
        return

    api_key = secret_sync("typesafe-api-key")
    batches = [
        pending[i : i + CLASSIFY_BATCH_SIZE] for i in range(0, len(pending), CLASSIFY_BATCH_SIZE)
    ]
    futures = classify_email_batch.map(batches, unmapped(api_key))
    classified = [c for batch in futures.result() for c in batch]
    total = persist_email_classifications(classified)
    low = sum(1 for c in classified if c.confidence is not None and c.confidence < 0.5)
    logger.info(f"classified {len(classified)} emails ({total} total); {low} below 0.5 confidence")


if __name__ == "__main__":
    classify_emails()

"""
LLM-classify unclassified inbox emails (personal / work / notification /
promotional) so scoring can down-weight promotional noise.

Chained: ingest fetches + persists raw emails, this flow enriches them, then
transform's dbt build joins the categories in. Split out of ingest so a
classifier outage can't fail ingestion of the other sources, and so ingest
stays pure fetch+persist.

Cache policy: each batch is keyed by its message_ids — a batch never hits the
LLM twice within 24h, so the common re-run is seconds.
"""

import datetime
import hashlib
import os
from dataclasses import dataclass
from typing import Any

from prefect import flow, get_run_logger, task, unmapped
from prefect.blocks.system import Secret
from prefect.cache_policies import CachePolicy
from prefect.context import TaskRunContext

from mps.db import unclassified_emails, write_email_classifications
from mps.email import EmailClassification, IndexedClassifications

# bump to invalidate cached classifications. "v3" matches the keys minted when
# this task lived in ingest, so the move doesn't re-classify the whole inbox.
_CACHE_VERSION = "v3"


def _db_path() -> str:
    return os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )

EMAIL_CLASSIFIER_PROMPT = """\
you categorize inbox emails for a solo developer's dashboard.
given numbered (sender, subject, snippet) lines, assign each index one:

- personal: written by a human directly to the recipient
- work: professional correspondence needing attention (invoices, contracts,
  recruiter/client mail, account security)
- notification: automated but informational (mailing lists, forum threads,
  CI results, statements ready, receipts)
- promotional: marketing, sales, product announcements, engagement bait

return a classification for every index you were given.
"""

CLASSIFY_BATCH_SIZE = 25


@dataclass
class ByEmailBatch(CachePolicy):
    """Cache key is the batch's message_ids — same batch never hits the LLM twice."""

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
def load_unclassified_emails() -> list[tuple[str, str, str, str]]:
    return unclassified_emails(_db_path())


@task(
    cache_policy=ByEmailBatch(),
    cache_expiration=datetime.timedelta(hours=24),
    persist_result=True,
    result_serializer="json",
)
def classify_email_batch(
    batch: list[tuple[str, str, str, str]], api_key: str
) -> list[EmailClassification]:
    """LLM-classify one batch of emails. Cached by the batch's message_ids."""
    from pydantic_ai import Agent
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    from mps.spend import record_pydantic_ai_result

    lines = [
        f"{i}. sender={sender!r} subject={subject!r} snippet={snippet[:200]!r}"
        for i, (_, sender, subject, snippet) in enumerate(batch)
    ]
    model = AnthropicModel(
        "claude-haiku-4-5", provider=AnthropicProvider(api_key=api_key)
    )
    agent = Agent(
        model,
        output_type=IndexedClassifications,
        system_prompt=EMAIL_CLASSIFIER_PROMPT,
        name="email-classifier",
        retries=2,
        model_settings={"anthropic_cache_instructions": "5m"},
    )
    result = agent.run_sync("classify these emails:\n\n" + "\n".join(lines))
    record_pydantic_ai_result(
        task_name="classify_email_batch",
        model="claude-haiku-4-5",
        result=result,
        metadata={"email_count": len(batch)},
    )
    return [
        EmailClassification(message_id=batch[c.index][0], category=c.category)
        for c in result.output.classifications
        if 0 <= c.index < len(batch)
    ]


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

    api_key = Secret.load("anthropic-api-key").get()
    batches = [
        pending[i : i + CLASSIFY_BATCH_SIZE]
        for i in range(0, len(pending), CLASSIFY_BATCH_SIZE)
    ]
    futures = classify_email_batch.map(batches, unmapped(api_key))
    classified = [c for batch in futures.result() for c in batch]
    total = persist_email_classifications(classified)
    logger.info(f"classified {len(classified)} emails ({total} total)")


if __name__ == "__main__":
    classify_emails()

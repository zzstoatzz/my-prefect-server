"""
Agentic review of phi's private observations.

Triggered by phi-tag-maintenance completion (event bus). Reviews the
per-user observations in TurboPuffer for contradictions, staleness, and
near-duplicates.

This flow does not touch phi's semble library. It used to run a janitor agent
over it daily; that agent deleted her notes, refiled cards against her own
shelving, and left orphaned links behind, all outside her telemetry. The
library has one curator: phi, live.
"""

from datetime import UTC, datetime
from typing import Any, cast

import turbopuffer
from mps.blocks import secret
from mps.observability import configure_logfire
from mps.phi import clean_handle
from mps.spend import record_openai_embedding_response, record_pydantic_ai_result
from openai import OpenAI
from prefect import flow, task
from prefect.cache_policies import NONE
from prefect.variables import Variable
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.providers.anthropic import AnthropicProvider

PERSONALITY_EXCERPT = """\
you are phi — a librarian who stepped outside. in this session you are back
inside, reviewing what you remember about the people you've talked to.

lowercase unless idiomatic. no filler.
"""

OBSERVATION_REVIEW_PROMPT = """\
review your private observations — the facts you've extracted from
conversations with people. use list_users to see who you have memory about,
then list_user_observations to inspect their observations.

you can use recall to cross-reference against other memory before deciding.

priorities:
- look for contradictions between observations about the same person
- look for observations that are clearly stale (someone's role or interest changed)
- look for near-duplicates that write-time reconciliation missed
- when in doubt, leave it alone — carrying a marginal observation is better than losing a real one

use deprecate_observation to remove things that are wrong or redundant.
use update_observation to correct or merge observations.

if everything looks clean, say so. quality over quantity.
"""


# ---------------------------------------------------------------------------
# deps & output
# ---------------------------------------------------------------------------


class CurationDeps(BaseModel, arbitrary_types_allowed=True):
    tpuf_client: Any = Field(description="turbopuffer client")
    openai_client: Any = Field(description="openai client for embeddings")

    model_config = {"arbitrary_types_allowed": True}


class CurationResult(BaseModel):
    summary: str = Field(description="brief summary of what you did (or why you did nothing)")
    actions_taken: int = Field(default=0, description="number of create/delete/modify actions")


# ---------------------------------------------------------------------------
# build agent with tools
# ---------------------------------------------------------------------------


def _build_agent(model_name: str, api_key: str) -> Agent[CurationDeps, CurationResult]:
    model = AnthropicModel(model_name, provider=AnthropicProvider(api_key=api_key))
    agent = Agent[CurationDeps, CurationResult](
        model,
        system_prompt=PERSONALITY_EXCERPT,
        output_type=CurationResult,
        deps_type=CurationDeps,
        name="phi-curator",
        # tool-heavy, multi-turn agent. caching both the system
        # prompt and the tool-definitions block is the highest-leverage spot
        # in the whole flow — every turn re-sends both, and every turn is now
        # a cache hit at 0.1× input price after the first.
        model_settings=cast(
            AnthropicModelSettings,
            {
                # Automatic caching lets Anthropic choose the moving cache point for
                # multi-turn agent history; the explicit breakpoints below keep the
                # stable prompt/tool prefixes cacheable when they clear the floor.
                "anthropic_cache": "5m",
                "anthropic_cache_instructions": "5m",
                "anthropic_cache_tool_definitions": "5m",
            },
        ),
    )

    @agent.tool
    async def recall(ctx: RunContext[CurationDeps], query: str, namespace: str = "") -> str:
        """Search your private memory (TurboPuffer). Leave namespace empty for broad search,
        or pass a handle like 'zzstoatzz.io' to search a specific user's namespace."""
        tpuf = ctx.deps.tpuf_client
        openai = ctx.deps.openai_client

        embedding_response = openai.embeddings.create(model="text-embedding-3-small", input=query)
        record_openai_embedding_response(
            task_name="curate.recall",
            model="text-embedding-3-small",
            response=embedding_response,
            item_count=1,
            metadata={"namespace": namespace},
        )
        embedding = embedding_response.data[0].embedding

        results: list[str] = []

        if namespace:
            # search specific user namespace
            clean = namespace.replace(".", "_").replace("@", "").replace("-", "_")
            ns = tpuf.namespace(f"phi-users-{clean}")
            try:
                resp = ns.query(
                    rank_by=("vector", "ANN", embedding),
                    top_k=8,
                    include_attributes=["content", "tags", "kind"],
                )
                for row in resp.rows or []:
                    kind = getattr(row, "kind", "")
                    tags = list(getattr(row, "tags", []) or [])
                    tag_str = f" [{', '.join(tags)}]" if tags else ""
                    results.append(f"[{kind}]{tag_str} {row.content}")
            except Exception:
                pass
        else:
            # search episodic
            try:
                ns = tpuf.namespace("phi-episodic")
                resp = ns.query(
                    rank_by=("vector", "ANN", embedding),
                    top_k=5,
                    include_attributes=["content", "tags"],
                )
                for row in resp.rows or []:
                    tags = list(getattr(row, "tags", []) or [])
                    tag_str = f" [{', '.join(tags)}]" if tags else ""
                    results.append(f"[episodic]{tag_str} {row.content}")
            except Exception:
                pass

            # search a few user namespaces
            try:
                page = tpuf.namespaces(prefix="phi-users-")
                for ns_summary in list(page.namespaces)[:10]:
                    handle = ns_summary.id.removeprefix("phi-users-").replace("_", ".")
                    ns = tpuf.namespace(ns_summary.id)
                    try:
                        resp = ns.query(
                            rank_by=("vector", "ANN", embedding),
                            top_k=3,
                            include_attributes=["content", "tags", "kind"],
                        )
                        for row in resp.rows or []:
                            kind = getattr(row, "kind", "")
                            results.append(f"[@{handle} {kind}] {row.content}")
                    except Exception:
                        continue
            except Exception:
                pass

        if not results:
            return "no relevant memories found"
        return "\n".join(results[:15])

    # --- observation curation tools ---

    @agent.tool
    async def list_users(ctx: RunContext[CurationDeps]) -> str:
        """List all user namespaces you have memory about."""
        tpuf = ctx.deps.tpuf_client
        try:
            page = tpuf.namespaces(prefix="phi-users-")
            handles = []
            for ns_summary in page.namespaces:
                handle = ns_summary.id.removeprefix("phi-users-").replace("_", ".")
                handles.append(f"@{handle}")
            if not handles:
                return "no user namespaces found"
            return f"{len(handles)} users:\n" + "\n".join(handles)
        except Exception as e:
            return f"failed to list users: {e}"

    @agent.tool
    async def list_user_observations(ctx: RunContext[CurationDeps], handle: str) -> str:
        """List all observations for a user. Shows content, tags, timestamps, and row ID."""
        tpuf = ctx.deps.tpuf_client
        ns_name = f"phi-users-{clean_handle(handle)}"
        ns = tpuf.namespace(ns_name)
        try:
            resp = ns.query(
                rank_by=("created_at", "desc"),
                top_k=50,
                filters=("kind", "Eq", "observation"),
                include_attributes=["content", "tags", "created_at"],
            )
            if not resp.rows:
                return f"no observations for @{handle}"
            lines = []
            for row in resp.rows:
                tags = list(getattr(row, "tags", []) or [])
                tag_str = f" [{', '.join(tags)}]" if tags else ""
                created = getattr(row, "created_at", "?")
                lines.append(f"id={row.id}{tag_str}\n  {row.content}\n  created: {created}")
            return f"{len(resp.rows)} observations for @{handle}:\n" + "\n".join(lines)
        except Exception as e:
            return f"failed to list observations for @{handle}: {e}"

    @agent.tool
    async def deprecate_observation(
        ctx: RunContext[CurationDeps], handle: str, observation_id: str, reason: str
    ) -> str:
        """Delete an observation from a user's namespace. Logs the reason."""
        tpuf = ctx.deps.tpuf_client
        ns_name = f"phi-users-{clean_handle(handle)}"
        ns = tpuf.namespace(ns_name)
        try:
            ns.write(deletes=[observation_id])
            return f"deprecated observation {observation_id} for @{handle}: {reason}"
        except Exception as e:
            return f"failed to deprecate {observation_id}: {e}"

    @agent.tool
    async def update_observation(
        ctx: RunContext[CurationDeps],
        handle: str,
        observation_id: str,
        new_content: str,
        new_tags: list[str],
    ) -> str:
        """Re-embed and overwrite an observation with corrected content. Sets fresh updated_at."""
        tpuf = ctx.deps.tpuf_client
        openai = ctx.deps.openai_client
        ns_name = f"phi-users-{clean_handle(handle)}"
        ns = tpuf.namespace(ns_name)

        embedding_response = openai.embeddings.create(
            model="text-embedding-3-small", input=new_content
        )
        record_openai_embedding_response(
            task_name="curate.update_observation",
            model="text-embedding-3-small",
            response=embedding_response,
            item_count=1,
            metadata={"handle": handle},
        )
        embedding = embedding_response.data[0].embedding

        now = datetime.now(UTC).isoformat()
        try:
            ns.write(
                upsert_rows=[
                    {
                        "id": observation_id,
                        "vector": embedding,
                        "kind": "observation",
                        "content": new_content,
                        "tags": new_tags,
                        "created_at": now,
                        "updated_at": now,
                    }
                ],
                distance_metric="cosine_distance",
                schema={
                    "kind": {"type": "string", "filterable": True},
                    "content": {"type": "string", "full_text_search": True},
                    "tags": {"type": "[]string", "filterable": True},
                    "created_at": {"type": "string"},
                    "updated_at": {"type": "string"},
                },
            )
            return f"updated observation {observation_id} for @{handle}: {new_content[:80]}"
        except Exception as e:
            return f"failed to update {observation_id}: {e}"

    return agent


# ---------------------------------------------------------------------------
# prefect tasks
# ---------------------------------------------------------------------------


@task(cache_policy=NONE)
async def run_observation_review(
    tpuf_client: Any,
    openai_client: Any,
    api_key: str,
    model_name: str,
) -> dict[str, Any]:
    """Run the observation review agent loop."""
    agent = _build_agent(model_name, api_key)
    deps = CurationDeps(
        tpuf_client=tpuf_client,
        openai_client=openai_client,
    )
    result = await agent.run(OBSERVATION_REVIEW_PROMPT, deps=deps)
    record_pydantic_ai_result(
        task_name="run_observation_review",
        model=model_name,
        result=result,
    )
    return result.output.model_dump()


# ---------------------------------------------------------------------------
# main flow
# ---------------------------------------------------------------------------


@flow(name="curate", log_prints=True, timeout_seconds=1800)
async def curate():
    """Phi reviews its private observations about the people it talks to."""
    configure_logfire("prefect-flow-curate")

    anthropic_key = await secret("anthropic-api-key")
    tpuf_key = await secret("turbopuffer-api-key")
    openai_key = await secret("openai-api-key")
    stored_model = await Variable.aget("curate-model")
    model_name = stored_model if isinstance(stored_model, str) else "claude-haiku-4-5"
    print(f"using model: {model_name}")

    tpuf_client = turbopuffer.Turbopuffer(api_key=tpuf_key, region="gcp-us-central1")
    openai_client = OpenAI(api_key=openai_key)
    obs_result = await run_observation_review(
        tpuf_client=tpuf_client,
        openai_client=openai_client,
        api_key=anthropic_key,
        model_name=model_name,
    )
    print(f"observation review: {obs_result['actions_taken']} actions — {obs_result['summary']}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(curate())

"""Keep the atproto docs MCP's Turbopuffer index in step with atproto.com.

The pipeline lives in tangled.org/zzstoatzz.io/atproto-docs-mcp and is
installed only in this deployment's job environment, so it is imported inside
the flow: registration loads this module without it.
"""

from prefect import flow


@flow(name="atproto-docs", log_prints=True)
async def atproto_docs(namespace: str = "atproto-docs-v1", force: bool = False) -> int:
    """re-index atproto.com when bluesky-social/atproto-website main has moved."""
    from atproto_docs_ingest.main import refresh_atproto_docs

    return await refresh_atproto_docs(namespace=namespace, force=force)

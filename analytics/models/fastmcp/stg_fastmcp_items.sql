-- one row per fastmcp issue or PR; contributor = neither a maintainer nor a bot.
-- bootstrap: the table exists even before fastmcp-warehouse has run
{{ config(materialized="view", pre_hook="CREATE TABLE IF NOT EXISTS raw_fastmcp_items ( number INTEGER PRIMARY KEY, kind VARCHAR, title VARCHAR, author VARCHAR, author_association VARCHAR, author_is_bot BOOLEAN, state VARCHAR, state_reason VARCHAR, is_draft BOOLEAN, head_ref VARCHAR, labels VARCHAR[], created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ, closed_at TIMESTAMPTZ, merged_at TIMESTAMPTZ, comment_count INTEGER, first_maintainer_response_at TIMESTAMPTZ, first_maintainer_responder VARCHAR, last_maintainer_activity_at TIMESTAMPTZ, triage_review_at TIMESTAMPTZ, fetched_at TIMESTAMPTZ DEFAULT now() )") }}
SELECT
    *,
    author_association IN ('OWNER', 'MEMBER', 'COLLABORATOR') AS by_maintainer,
    NOT author_is_bot AND author_association NOT IN ('OWNER', 'MEMBER', 'COLLABORATOR') AS by_contributor,
    timezone('America/Chicago', created_at)::DATE AS created_day,
    timezone('America/Chicago', closed_at)::DATE AS closed_day,
    timezone('America/Chicago', merged_at)::DATE AS merged_day
FROM {{ source('raw', 'raw_fastmcp_items') }}

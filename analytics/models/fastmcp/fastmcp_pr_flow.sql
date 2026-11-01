-- one row per contributor PR: how long until a maintainer answered, and how it ended.
-- a merge answers too: only a maintainer can merge, often without commenting first
SELECT
    number,
    title,
    author,
    author_association,
    state,
    is_draft,
    created_at,
    created_day,
    merged_at,
    closed_at,
    triage_review_at IS NOT NULL AS triage_reviewed,
    triage_review_at,
    first_maintainer_response_at,
    first_maintainer_responder,
    last_maintainer_activity_at,
    least(first_maintainer_response_at, merged_at) AS first_response_at,
    date_diff('minute', created_at, least(first_maintainer_response_at, merged_at)) / 60.0
        AS hours_to_first_response,
    date_diff('minute', created_at, coalesce(merged_at, closed_at)) / 60.0 AS hours_to_resolution,
    date_diff('day', created_at, now()) AS age_days
FROM {{ ref('stg_fastmcp_items') }}
WHERE kind = 'pr' AND by_contributor

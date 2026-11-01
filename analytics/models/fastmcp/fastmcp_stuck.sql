-- open, ready contributor PRs no maintainer has answered in a week, or gone quiet for two
SELECT
    number,
    title,
    author,
    created_at,
    age_days,
    triage_reviewed,
    first_maintainer_response_at,
    last_maintainer_activity_at,
    CASE
        WHEN first_maintainer_response_at IS NULL THEN 'no maintainer response'
        ELSE 'maintainer quiet'
    END AS reason,
    'https://github.com/PrefectHQ/fastmcp/pull/' || number AS url
FROM {{ ref('fastmcp_pr_flow') }}
WHERE state = 'OPEN'
  AND NOT is_draft
  AND (
    (first_maintainer_response_at IS NULL AND age_days >= 7)
    OR last_maintainer_activity_at < now() - INTERVAL 14 DAY
  )
ORDER BY age_days DESC

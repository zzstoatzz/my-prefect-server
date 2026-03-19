SELECT
    repo, number, type, title, url, "user", labels,
    comments, reactions_total, importance_score,
    updated_at
FROM {{ ref('int_github_issues_scored') }}
ORDER BY importance_score DESC
LIMIT 50

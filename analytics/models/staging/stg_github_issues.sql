-- dedup by repo+number, keep most recent fetch
SELECT DISTINCT ON (repo, number)
    repo, number, type, title, state, body, url,
    labels, created_at, updated_at, "user",
    comments, reactions_total, fetched_at
FROM {{ source('raw', 'raw_github_issues') }}
ORDER BY repo, number, fetched_at DESC

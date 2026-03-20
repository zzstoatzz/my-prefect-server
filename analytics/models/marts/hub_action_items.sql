SELECT source, repo, identifier, kind, title, url,
       author, labels, importance_score, updated
FROM (
    SELECT
        'github' AS source,
        repo,
        number::VARCHAR AS identifier,
        type AS kind,
        title,
        url,
        "user" AS author,
        labels,
        importance_score,
        updated_at AS updated
    FROM {{ ref('int_github_issues_scored') }}

    UNION ALL

    SELECT
        'tangled' AS source,
        repo,
        SPLIT_PART(at_uri, '/', -1) AS identifier,
        kind,
        title,
        url,
        author_handle AS author,
        ARRAY[]::VARCHAR[] AS labels,
        importance_score,
        created_at AS updated
    FROM {{ ref('int_tangled_items_scored') }}
)
ORDER BY importance_score DESC
LIMIT 200

-- each source scores on its own scale (github's contributor multiplier reaches
-- ~2x, email caps at 1.0), so raw scores aren't comparable across sources.
-- min-max normalize within each source before the global top-200 cut, so no
-- source can crowd the others out on scale alone.
WITH unioned AS (
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

    UNION ALL

    SELECT
        'email' AS source,
        'inbox' AS repo,
        message_id AS identifier,
        'email' AS kind,
        subject AS title,
        'https://mail.proton.me/u/0/inbox' AS url,
        sender_address AS author,
        CASE WHEN unread THEN ARRAY['unread'] ELSE ARRAY[]::VARCHAR[] END AS labels,
        importance_score,
        received_at AS updated
    FROM {{ ref('int_emails_scored') }}
)
SELECT source, repo, identifier, kind, title, url, author, labels,
    ROUND(
        CASE
            WHEN MAX(importance_score) OVER (PARTITION BY source)
               = MIN(importance_score) OVER (PARTITION BY source)
            THEN importance_score
            ELSE (importance_score - MIN(importance_score) OVER (PARTITION BY source))
               / (MAX(importance_score) OVER (PARTITION BY source)
                - MIN(importance_score) OVER (PARTITION BY source))
        END,
        4
    ) AS importance_score,
    updated
FROM unioned
ORDER BY importance_score DESC
LIMIT 200

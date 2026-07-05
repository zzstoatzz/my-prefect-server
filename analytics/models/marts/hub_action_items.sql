-- each source scores on its own scale (github's contributor multiplier reaches
-- ~2x, email caps at 1.0), so raw scores aren't comparable across sources.
-- deflate any source whose max exceeds 1 before the global top-200 cut, so no
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
        COALESCE(NULLIF(sender_name, ''), sender_address) AS author,
        (CASE WHEN unread THEN ARRAY['unread'] ELSE ARRAY[]::VARCHAR[] END)
            || (CASE WHEN is_bulk THEN ARRAY['bulk'] ELSE ARRAY[]::VARCHAR[] END) AS labels,
        importance_score,
        received_at AS updated
    FROM {{ ref('int_emails_scored') }}
)
SELECT source, repo, identifier, kind, title, url, author, labels,
    -- deflate sources whose scale exceeds 1 (github's contributor multiplier)
    -- WITHOUT stretching low scores up: min-max would promote a source's best
    -- item to 1.0 even when it's absolute junk (e.g. an inbox of pure spam)
    ROUND(
        importance_score
            / GREATEST(MAX(importance_score) OVER (PARTITION BY source), 1.0),
        4
    ) AS importance_score,
    updated
FROM unioned
ORDER BY importance_score DESC
LIMIT 200
